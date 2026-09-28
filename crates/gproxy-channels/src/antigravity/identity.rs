use bytes::Bytes;
use gproxy_channel_api::ChannelError;
use http::{HeaderMap, HeaderName, HeaderValue};
use serde_json::Value;
use sha2::{Digest, Sha256};

/// Request-scoped Antigravity identity.
///
/// The upstream-visible identifiers are one-way derivatives. In particular,
/// neither a downstream session id nor credential material is copied onto the
/// wire. The machine id is credential-scoped instead of host-scoped so a
/// multi-account gateway does not advertise one shared server fingerprint.
pub(super) struct RuntimeIdentity {
    conversation: String,
    machine_id: String,
    session_id: String,
    vscode_session_id: String,
}

impl RuntimeIdentity {
    pub(super) fn new(
        secret: &Value,
        downstream_session: Option<&str>,
    ) -> Result<Self, ChannelError> {
        let project = super::auth::project_id(secret)?;
        let account = secret
            .get("user_email")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or(project);
        let conversation = match downstream_session
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            Some(value) => value.to_owned(),
            None => random_uuid()?,
        };
        let account_seed = format!("antigravity-account-v1:{project}:{account}");
        let session_seed = format!("antigravity-session-v1:{account_seed}:{conversation}");
        Ok(Self {
            conversation: hash_hex(&session_seed)[..16].to_owned(),
            machine_id: uuid_from_hash(&account_seed),
            session_id: negative_i64(&session_seed),
            vscode_session_id: uuid_from_hash(&session_seed),
        })
    }

    pub(super) fn apply_headers(
        &self,
        headers: &mut HeaderMap,
        version: &str,
    ) -> Result<(), ChannelError> {
        insert(headers, "x-client-name", "antigravity")?;
        insert(headers, "x-client-version", version)?;
        insert(headers, "x-machine-id", &self.machine_id)?;
        insert(headers, "x-vscode-sessionid", &self.vscode_session_id)?;
        Ok(())
    }

    pub(super) fn wrap_generation(&self, body: &Bytes, model: &str) -> Result<Bytes, ChannelError> {
        let mut value: Value = serde_json::from_slice(body).map_err(|error| {
            ChannelError::Prepare(format!("Code Assist envelope JSON: {error}"))
        })?;
        let object = value.as_object_mut().ok_or_else(|| {
            ChannelError::Prepare("Code Assist envelope must be an object".into())
        })?;

        // `user_prompt_id` belonged to an older Code Assist client shape. The
        // current Antigravity clients use requestId + request.sessionId.
        object.remove("user_prompt_id");
        object.insert("userAgent".into(), Value::String("antigravity".into()));

        let inner = object
            .get_mut("request")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| ChannelError::Prepare("Code Assist request must be an object".into()))?;
        inner.insert("sessionId".into(), Value::String(self.session_id.clone()));

        let step = inner
            .get("contents")
            .and_then(Value::as_array)
            .map(|contents| contents.len() as u64)
            .unwrap_or(0);
        let agent = has_agent_work(inner);
        let image = model.to_ascii_lowercase().contains("image");

        object.insert(
            "requestId".into(),
            Value::String(if image {
                format!("image_gen/{}/{}/12", unix_ms(), random_uuid()?)
            } else {
                format!(
                    "agent/{}/{}/{}/{}",
                    self.conversation,
                    unix_ms(),
                    random_hex(4)?,
                    step
                )
            }),
        );
        if image {
            object.insert("requestType".into(), Value::String("image_gen".into()));
        } else if agent {
            object.insert("requestType".into(), Value::String("agent".into()));
        } else {
            object.remove("requestType");
        }

        serde_json::to_vec(&value)
            .map(Bytes::from)
            .map_err(|error| ChannelError::Prepare(error.to_string()))
    }
}

pub(super) fn client_version(user_agent: &str) -> &str {
    let lower = user_agent.to_ascii_lowercase();
    for prefix in ["antigravity/hub/", "antigravity/"] {
        if lower.starts_with(prefix) {
            let value = &user_agent[prefix.len()..];
            if let Some(version) = value.split_ascii_whitespace().next()
                && !version.is_empty()
            {
                return version;
            }
        }
    }
    super::profile::DEFAULT_VERSION
}

fn has_agent_work(request: &serde_json::Map<String, Value>) -> bool {
    let has_tools = request.get("tools").is_some_and(|tools| match tools {
        Value::Array(tools) => !tools.is_empty(),
        Value::Object(tools) => !tools.is_empty(),
        _ => false,
    });
    has_tools
        || request
            .get("contents")
            .and_then(Value::as_array)
            .is_some_and(|contents| {
                contents.iter().any(|content| {
                    content
                        .get("parts")
                        .and_then(Value::as_array)
                        .is_some_and(|parts| {
                            parts.iter().any(|part| {
                                part.get("functionCall").is_some()
                                    || part.get("functionResponse").is_some()
                            })
                        })
                })
            })
}

fn insert(headers: &mut HeaderMap, name: &'static str, value: &str) -> Result<(), ChannelError> {
    headers.insert(
        HeaderName::from_static(name),
        HeaderValue::from_str(value)
            .map_err(|_| ChannelError::Prepare(format!("invalid Antigravity {name}")))?,
    );
    Ok(())
}

fn hash_hex(value: &str) -> String {
    Sha256::digest(value.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn negative_i64(seed: &str) -> String {
    let digest = Sha256::digest(seed.as_bytes());
    let mut bytes = [0_u8; 8];
    bytes.copy_from_slice(&digest[..8]);
    let magnitude = (u64::from_be_bytes(bytes) & i64::MAX as u64).max(1);
    format!("-{}", magnitude)
}

fn random_uuid() -> Result<String, ChannelError> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes)
        .map_err(|_| ChannelError::Prepare("Antigravity identity randomness failed".into()))?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(uuid_from_digest(bytes))
}

fn uuid_from_hash(seed: &str) -> String {
    let digest = Sha256::digest(seed.as_bytes());
    let mut bytes = [0_u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    uuid_from_digest(bytes)
}

fn random_hex(len: usize) -> Result<String, ChannelError> {
    let mut bytes = vec![0_u8; len];
    getrandom::fill(&mut bytes)
        .map_err(|_| ChannelError::Prepare("Antigravity request randomness failed".into()))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn uuid_from_digest(mut bytes: [u8; 16]) -> String {
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0],
        bytes[1],
        bytes[2],
        bytes[3],
        bytes[4],
        bytes[5],
        bytes[6],
        bytes[7],
        bytes[8],
        bytes[9],
        bytes[10],
        bytes[11],
        bytes[12],
        bytes[13],
        bytes[14],
        bytes[15]
    )
}

fn unix_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn identities_are_stable_per_account_and_conversation_without_leaking_input() {
        let secret = json!({"project_id":"project-1","user_email":"user@example.com"});
        let first = RuntimeIdentity::new(&secret, Some("private-session-1")).unwrap();
        let second = RuntimeIdentity::new(&secret, Some("private-session-1")).unwrap();
        let other = RuntimeIdentity::new(&secret, Some("private-session-2")).unwrap();
        assert_eq!(first.machine_id, second.machine_id);
        assert_eq!(first.session_id, second.session_id);
        assert_eq!(first.vscode_session_id, second.vscode_session_id);
        assert_ne!(first.session_id, other.session_id);
        assert!(!first.session_id.contains("private-session"));
        assert!(first.session_id.parse::<i64>().unwrap().is_negative());
    }

    #[test]
    fn generation_envelope_uses_current_antigravity_shape() {
        let identity =
            RuntimeIdentity::new(&json!({"project_id":"project-1"}), Some("conversation-1"))
                .unwrap();
        let body = Bytes::from_static(
            br#"{"model":"gemini-3","project":"p","user_prompt_id":"legacy","request":{"contents":[{"role":"user","parts":[{"text":"hi"}]}],"tools":[{"functionDeclarations":[{"name":"run"}]}]}}"#,
        );
        let wrapped: Value =
            serde_json::from_slice(&identity.wrap_generation(&body, "gemini-3").unwrap()).unwrap();
        assert!(wrapped.get("user_prompt_id").is_none());
        assert_eq!(wrapped["userAgent"], "antigravity");
        assert_eq!(wrapped["requestType"], "agent");
        assert!(wrapped["requestId"].as_str().unwrap().starts_with("agent/"));
        assert!(
            wrapped["request"]["sessionId"]
                .as_str()
                .unwrap()
                .parse::<i64>()
                .unwrap()
                .is_negative()
        );
    }

    #[test]
    fn extracts_supported_user_agent_versions() {
        assert_eq!(
            client_version("antigravity/hub/2.17.0 darwin/arm64"),
            "2.17.0"
        );
        assert_eq!(client_version("Antigravity/4.3.0 (...)"), "4.3.0");
        assert_eq!(
            client_version("custom/1"),
            super::super::profile::DEFAULT_VERSION
        );
    }
}
