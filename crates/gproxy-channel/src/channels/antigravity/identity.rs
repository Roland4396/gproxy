//! Preserve v3's account-scoped runtime headers without overriding v4's
//! captured CLI envelope grammar or a session supplied by the caller.
use http::{HeaderMap, HeaderValue};
use sha2::{Digest, Sha256};

use crate::channel::{ChannelError, CredentialView};

fn identifier(seed: &str) -> String {
    let hash = Sha256::digest(seed.as_bytes());
    let mut bytes: [u8; 16] = hash[..16].try_into().expect("sixteen hash bytes");
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
    format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..])
}

pub(super) fn apply(
    headers: &mut HeaderMap,
    credential: &CredentialView<'_>,
    session: Option<&str>,
) -> Result<(), ChannelError> {
    let project = super::fact(credential, "project_id").unwrap_or(credential.id);
    let account = super::fact(credential, "user_email").unwrap_or(project);
    let account_seed = format!("antigravity-account-v1:{project}:{account}");
    let machine = identifier(&account_seed);
    let session_seed = format!("antigravity-session-v1:{account_seed}:{}", session.unwrap_or("metadata"));
    let session = identifier(&session_seed);
    let version = super::CLI_USER_AGENT.strip_prefix("antigravity/cli/")
        .and_then(|s| s.split_ascii_whitespace().next()).unwrap_or("1.2.16");
    for (name, value) in [
        ("x-client-name", "antigravity"), ("x-client-version", version),
        ("x-machine-id", machine.as_str()), ("x-vscode-sessionid", session.as_str()),
    ] {
        headers.insert(name, HeaderValue::from_str(value).map_err(|_| ChannelError::InvalidCredential)?);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn stable_per_account_and_session_without_plaintext_identity_on_wire() {
        let secret = json!({"project_id":"project-private","user_email":"account-private@example.test"});
        let metadata = json!({});
        let credential = CredentialView { id:"credential",provider_id:"provider",auth_kind:"oauth",
            secret:&secret,metadata:&metadata,version:0,expires_at_ms:None };
        let mut a=HeaderMap::new();let mut b=HeaderMap::new();let mut c=HeaderMap::new();
        apply(&mut a,&credential,Some("private-conversation-a")).unwrap();
        apply(&mut b,&credential,Some("private-conversation-a")).unwrap();
        apply(&mut c,&credential,Some("private-conversation-b")).unwrap();
        assert_eq!(a,b);assert_eq!(a["x-machine-id"],c["x-machine-id"]);
        assert_ne!(a["x-vscode-sessionid"],c["x-vscode-sessionid"]);
        for value in a.values() {assert!(!value.to_str().unwrap().contains("private"));}
        let other = json!({"project_id":"project-private","user_email":"other@example.test"});
        let mut d=HeaderMap::new();apply(&mut d,&CredentialView {secret:&other,..credential},Some("private-conversation-a")).unwrap();
        assert_ne!(a["x-machine-id"],d["x-machine-id"]);
    }
}
