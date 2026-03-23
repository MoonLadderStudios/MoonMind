with open("tests/unit/services/test_manifests_service.py", "r") as f:
    c = f.read()
c = c.replace("""service = ManifestsService(
                session,
                None,
                execution_service=execution_service,
                artifact_service=artifact_service,
            )""", """service = ManifestsService(
                session,
                execution_service=execution_service,
                artifact_service=artifact_service,
            )""")
with open("tests/unit/services/test_manifests_service.py", "w") as f:
    f.write(c)
