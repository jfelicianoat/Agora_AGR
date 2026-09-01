# F5 Acceptance Report — OAuth M2M and AI Gateway

Date: 2026-09-01

## Outcome

F5 is implemented and ready for the mandatory human gate. Agora now supports short-lived,
scoped OAuth 2.0 client-credentials tokens authenticated with `private_key_jwt`. A separate
TLS Gateway exposes the AI_Broker contract without importing or owning Agora's Board, CARD
state machine, or a second work queue.

The Gateway's internal `X-Admin-Token` is generated and held only by the local broker
supervisor. OAuth clients never receive it. If AI_Broker restarts and rotates that token, the
Gateway renews its local broker client and retries the failed operation once.

## Security boundary

- Access tokens are RS256 JWT access tokens with `typ=at+jwt`, `iss`, `sub`, `aud`, `exp`,
  `iat`, `jti`, `client_id`, `scope`, and a client-key version.
- Token lifetime is constrained to 30–900 seconds; the default is 300 seconds.
- Client authentication uses one-time, short-lived `private_key_jwt` assertions.
- Agora scopes: `cards:read`, `cards:write`, `board:claim`, `board:admin`.
- Gateway scopes: `gateway:read`, `gateway:submit`, `gateway:cancel`.
- Revoking or rotating one client invalidates only that client's tokens.
- Individual access-token revocation is immediate.
- OAuth and Gateway endpoints require HTTPS in production mode.
- Request-supplied task origin is rejected at the top level and overwritten inside broker
  metadata with the authenticated OAuth subject.
- Broker errors are translated to stable external errors; broker authentication failures
  become `503 broker_auth_unavailable` after the one allowed renewal attempt.

The implementation follows the relevant IETF contracts: RFC 6749 client credentials,
RFC 7523 JWT client authentication, RFC 9068 JWT access-token profile, and RFC 7009 token
revocation.

## Required F5 test matrix

| # | Acceptance condition | Automated evidence | Result |
|---:|---|---|---|
| 1 | Missing token rejected | `test_oauth_client_credentials_rejects_missing_and_expired_tokens` | Pass |
| 2 | Expired token rejected | `test_oauth_client_credentials_rejects_missing_and_expired_tokens` | Pass |
| 3 | Wrong audience rejected | `test_oauth_rejects_wrong_audience_and_insufficient_scope` | Pass |
| 4 | Insufficient scope rejected | `test_oauth_rejects_wrong_audience_and_insufficient_scope` | Pass |
| 5 | Spoofed origin rejected or ignored | `test_oauth_origin_is_authenticated_identity_and_spoof_is_rejected`; `test_gateway_rejects_top_level_origin_and_overwrites_nested_spoof` | Pass |
| 6 | Final origin is authenticated identity | Same origin tests above | Pass |
| 7 | Revoking one client leaves others valid | `test_revoking_one_oauth_client_does_not_affect_another` | Pass |
| 8 | Broker token renews dynamically after restart | `test_gateway_renews_dynamic_broker_token_once_after_restart` | Pass |
| 9 | OAuth client never knows `X-Admin-Token` | `test_gateway_requires_oauth_scope_and_never_exposes_admin_token` | Pass |
| 10 | Gateway returns semantic errors | `test_gateway_maps_broker_errors_semantically` | Pass |
| 11 | Gateway works without CARD/Board | `test_gateway_is_board_independent_and_has_no_second_queue` | Pass |
| 12 | Agora worker can use Gateway without recursion/double queue | `test_broker_executor_can_use_gateway_adapter_without_recursive_submission` | Pass |

Additional OAuth coverage verifies client key rotation, assertion replay rejection, and
immediate access-token revocation.

## Verification

- OAuth-specific tests: 6 passed.
- Gateway-specific tests: 6 passed.
- API/OAuth/Gateway focused regression: 23 passed.
- Full Agora suite: 81 passed.
- Python bytecode compilation: passed.
- Git whitespace validation: passed.
- Ruff and mypy were not available in the installed interpreter, so their project checks
  could not be executed in this environment. Changed Python files were separately checked
  for the configured 100-character line limit.

No AI_Broker or Athena source file was modified. No supplied production credential was used,
stored, printed, or committed.

## Operations

Initialize server signing material and an empty client registry:

```powershell
agora-oauth init-authority --registry .agora/oauth-clients.json --signing-key .agora/oauth-signing.pem
```

Register a Gateway client (the generated private key belongs on that client only):

```powershell
agora-oauth register-client agora-worker `
  --registry .agora/oauth-clients.json `
  --private-key .agora/agora-worker.pem `
  --scope gateway:read --scope gateway:submit --scope gateway:cancel `
  --audience agora-gateway
```

The `rotate-client` and `revoke-client` commands provide per-client lifecycle control. The
`agora-api` command accepts `--issuer`, `--oauth-clients`, and `--oauth-signing-key`. The
`agora-gateway` command additionally supervises a loopback-only AI_Broker child and serves
the external contract over TLS.

## Human gate

Do not begin F6/A2A until this F5 report is explicitly approved.
