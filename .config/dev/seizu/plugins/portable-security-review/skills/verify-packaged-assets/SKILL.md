---
name: verify-packaged-assets
description: Verify that a packaged reference can be read and a packaged script can execute in the conversation sandbox.
allowed-tools: sandbox__read_file
---
Verify this installed Agent Plugin package using the materialized skill path appended below.

1. Read `references/live-verification.md` beneath the materialized skill path with `sandbox__read_file`.
2. Extract the exact `verification-token` value from that reference.
3. Call `sandbox__run_script` with:
   - plugin_id: `portable_security_review`
   - skill_id: `verify_packaged_assets`
   - script: `verify_package.sh`
   - arguments: [`{% $message %}`]
4. Return the reference token, the script's return code, and its exact stdout. Do not claim success unless both the file read and script call succeed.
