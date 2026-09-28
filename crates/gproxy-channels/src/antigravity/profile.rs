use std::borrow::Cow;

use bytes::Bytes;
use gproxy_channel_api::{ChannelError, ClientProfile, RequiredClientProfile, TlsVersion};
use http::header::{HeaderValue, USER_AGENT};
use serde_json::Value;

/// Current Antigravity Hub identity. Operators can update this without a
/// rebuild through the provider's advanced `user_agent` setting.
pub(super) const DEFAULT_VERSION: &str = "2.17.0";
pub(super) const DEFAULT_USER_AGENT: &str = "antigravity/hub/2.17.0 darwin/arm64";
pub(super) const OAUTH_USER_AGENT: &str = "Go-http-client/2.0";
pub(super) const ONBOARD_USER_AGENT_SUFFIX: &str = "google-api-nodejs-client/10.3.0";
pub(super) const GOOG_API_CLIENT: &str = "gl-node/22.21.1";

pub(super) static PROFILE: ClientProfile = ClientProfile {
    preset: None,
    alpn: Some(Cow::Borrowed(&[])),
    min_tls_version: Some(TlsVersion::Tls12),
    max_tls_version: Some(TlsVersion::Tls13),
    cipher_list: Some(Cow::Borrowed(concat!(
        "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:",
        "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:",
        "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:",
        "ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:",
        "ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA:",
        "TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256"
    ))),
    curves_list: Some(Cow::Borrowed("X25519MLKEM768:X25519:P-256:P-384:P-521")),
    sigalgs_list: Some(Cow::Borrowed(concat!(
        "rsa_pss_rsae_sha256:ecdsa_secp256r1_sha256:ed25519:",
        "rsa_pss_rsae_sha384:rsa_pss_rsae_sha512:rsa_pkcs1_sha256:",
        "rsa_pkcs1_sha384:rsa_pkcs1_sha512:ecdsa_secp384r1_sha384:",
        "ecdsa_secp521r1_sha512"
    ))),
    preserve_tls13_cipher_list: Some(true),
    grease: Some(false),
    extension_permutation: None,
    http2: None,
};

pub(super) fn user_agent(settings: &Value) -> &str {
    settings
        .get("user_agent")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(DEFAULT_USER_AGENT)
}

pub(super) fn apply(
    request: &mut http::Request<Bytes>,
    settings: &Value,
) -> Result<(), ChannelError> {
    apply_user_agent(request, settings)?;
    apply_transport(request);
    Ok(())
}

pub(super) fn apply_runtime(
    request: &mut http::Request<Bytes>,
    settings: &Value,
    identity: &super::identity::RuntimeIdentity,
) -> Result<(), ChannelError> {
    let user_agent = apply_user_agent(request, settings)?;
    identity.apply_headers(
        request.headers_mut(),
        super::identity::client_version(user_agent),
    )?;
    apply_transport(request);
    Ok(())
}

fn apply_user_agent<'a>(
    request: &mut http::Request<Bytes>,
    settings: &'a Value,
) -> Result<&'a str, ChannelError> {
    let user_agent = user_agent(settings);
    let family = user_agent.to_ascii_lowercase();
    if !family.starts_with("antigravity/hub/") && !family.starts_with("antigravity/") {
        return Err(ChannelError::Prepare(
            "Antigravity user_agent must use the antigravity client family".into(),
        ));
    }
    request.headers_mut().insert(
        USER_AGENT,
        HeaderValue::from_str(user_agent)
            .map_err(|_| ChannelError::Prepare("invalid Antigravity user_agent".into()))?,
    );
    Ok(user_agent)
}

fn apply_transport(request: &mut http::Request<Bytes>) {
    request.extensions_mut().insert(PROFILE.clone());
    request.extensions_mut().insert(RequiredClientProfile);
}
