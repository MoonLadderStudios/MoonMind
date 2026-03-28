# Tasks: 106-secrets-ui-api

- [x] T001 Update `api_service/api/schemas.py` for Secret requests/responses (DOC-REQ-001)
- [x] T002 Update `api_service/services/secrets.py` to add `delete_secret` (DOC-REQ-001)
- [x] T003 Implement `api_service/api/routers/secrets.py` (DOC-REQ-001)
- [x] T004 Mount `secrets.py` router in `api_service/main.py`
- [x] T005 Update `tests/unit/api/test_secrets_api.py` to validate API boundaries and ensure `ciphertext` stays suppressed
- [x] T006 Implement `frontend/src/routes/secrets` Dashboard page showing metadata safely (DOC-REQ-002)
- [x] T007 Implement `frontend/src/components/secrets/SecretManager.tsx` component for Add/Rotate/Delete (DOC-REQ-002)
- [x] T008 Implement First-Run Setup alert for crucial Provider API Keys in `frontend/src/routes/dashboard` (DOC-REQ-003)
- [x] T009 Implement Provider Profile secret mapping validation in UI (DOC-REQ-004)
