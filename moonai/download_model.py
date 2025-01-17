import os

from transformers import AutoModel, AutoTokenizer


def download_model(hf_access_token, model_directory, model_name):
    """
    Downloads a Hugging Face model into a specified directory.

    Args:
        hf_access_token (str): Hugging Face access token for authentication.
        model_directory (str): Directory to save the downloaded model.
        model_name (str): Model identifier on Hugging Face.
    """
    try:
        # Ensure the directory exists
        os.makedirs(model_directory, exist_ok=True)

        print(f"Downloading model '{model_name}' into '{model_directory}'...")
        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            token=hf_access_token,
            cache_dir=model_directory
        )
        model = AutoModel.from_pretrained(
            model_name,
            trust_remote_code=True,
            token=hf_access_token,
            cache_dir=model_directory
        )

        print(f"Model '{model_name}' successfully downloaded into '{model_directory}'!")
        return True

    except Exception as e:
        print(f"An error occurred: {e}")
        return False
