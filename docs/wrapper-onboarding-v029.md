# HomeServer Wrapper Onboarding v0.29

HomeServer keeps one private capability server while allowing multiple independent front-end wrappers such as VP3 and Microgifter.

## Stable wrapper identity

Every wrapper must use a stable `app_key` and human-readable `app_name`. Do not reuse another product's `app_key`. Current canonical identities include `vp3` and `microgifter`.

## Claim-v1 pairing

1. Read `/api/v1/capabilities` and confirm `pairing_protocol` is `claim-v1`.
2. Submit `app_key`, `app_name`, and the minimum coarse `permissions` required to `/api/v1/pairing/request`.
3. Show the one-time pairing code to the user.
4. The HomeServer owner reviews and approves the code locally.
5. Poll `/api/v1/pairing/status` using the returned `request_id` and `claim_token`.
6. When approved, the claim token becomes the bearer credential for that wrapper only. Store it encrypted on the wrapper's server side; never expose it to browser JavaScript or logs.

## Owner-controlled boundaries

Coarse permissions grant capabilities. HomeServer application scopes can only narrow those permissions:

- cloud-backed inference allowed or blocked
- Memory key prefixes
- Knowledge kinds
- Tool keys
- Plugin keys

An empty resource boundary means all resources already permitted by the corresponding coarse permission. It never grants a missing permission.

## Re-pair and credential rotation

The owner can require an application to re-pair. HomeServer immediately revokes the current credential. The wrapper then repeats claim-v1 pairing. Re-pairing rotates the credential and preserves the owner's existing resource scope for that stable `app_key`.

## Capability changes

New HomeServer capabilities are not automatically granted to existing wrappers. A wrapper must request the corresponding coarse permission and the owner must approve the request. The Connected Apps control surface highlights re-pair requests that ask for permissions beyond the wrapper's current grant.

## Privacy and audit

The Connected Apps activity view intentionally excludes bearer credentials, prompt text, Memory contents, Knowledge contents, and private scope values. It records only safe operational metadata such as application identity, action type, resource category, status changes, and timestamps.
