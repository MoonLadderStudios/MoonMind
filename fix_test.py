with open("tests/unit/config/test_settings.py", "r") as f:
    code = f.read()

code = code.replace(
    """def test_default_model_fields_removed(app_settings_defaults):
    with pytest.raises(ValidationError):
        AppSettings(
            **app_settings_defaults, default_chat_model="some-model"
        )  # This should fail
    with pytest.raises(ValidationError):
        AppSettings(
            **app_settings_defaults, default_embed_model="some-model"
        )  # This should fail

    # Check that they are not present as attributes either
    settings = AppSettings(**app_settings_defaults)
    assert not hasattr(settings, "default_chat_model")
    assert not hasattr(settings, "default_embed_model")""",
    """def test_default_model_fields_removed(app_settings_defaults):
    # Since extra="ignore", these should just be ignored, not raise ValidationError
    s1 = AppSettings(**app_settings_defaults, default_chat_model="some-model")
    s2 = AppSettings(**app_settings_defaults, default_embed_model="some-model")

    # Check that they are not present as attributes
    assert not hasattr(s1, "default_chat_model")
    assert not hasattr(s2, "default_embed_model")

    settings = AppSettings(**app_settings_defaults)
    assert not hasattr(settings, "default_chat_model")
    assert not hasattr(settings, "default_embed_model")""",
)

with open("tests/unit/config/test_settings.py", "w") as f:
    f.write(code)
