import joblib
import numpy as np
import pandas as pd


MODEL_PATH = "company_model.pkl"
ENCODER_PATH = "industry_encoder.pkl"
INDUSTRY_FEATURE_NAMES_PATH = "industry_feature_names.pkl"
STATUS_LABEL_ENCODER_PATH = "status_label_encoder.pkl"


def _has_ltd(company_name):
    text = str(company_name or "")
    return 1 if ("Ltd" in text or "Pvt" in text) else 0


def _gst_length(gst_no):
    return len(str(gst_no or ""))


def _is_gst_valid(gst_no):
    return 1 if _gst_length(gst_no) == 15 else 0


def _registration_as_float(registration_no):
    try:
        return float(registration_no)
    except (TypeError, ValueError):
        return 0.0


def build_feature_vector(company_name, industry, registration_no, gst_no):
    model = joblib.load(MODEL_PATH)
    encoder = joblib.load(ENCODER_PATH)
    saved_feature_names = joblib.load(INDUSTRY_FEATURE_NAMES_PATH)

    # Use DataFrame with the same column name used during training to avoid feature-name warnings.
    industry_df = pd.DataFrame([{"Industry": industry}])
    try:
        industry_encoded = encoder.transform(industry_df)
        industry_encoded = np.asarray(industry_encoded, dtype=float)
    except ValueError:
        # Unknown category with older encoders (handle_unknown='error'): keep all-zero one-hot.
        industry_encoded = np.zeros((1, len(saved_feature_names)), dtype=float)

    # Keep strict feature ordering used in training:
    # [industry_one_hot..., RegistrationNo, GST_Length, Is_GST_Valid, Has_Ltd]
    engineered = np.array(
        [[
            _registration_as_float(registration_no),
            float(_gst_length(gst_no)),
            float(_is_gst_valid(gst_no)),
            float(_has_ltd(company_name)),
        ]],
        dtype=float,
    )
    X = np.hstack([industry_encoded, engineered])

    expected_dim = len(saved_feature_names) + 4
    if X.shape[1] != expected_dim:
        raise ValueError(
            f"Feature mismatch: got {X.shape[1]} columns, expected {expected_dim}. "
            "Re-train model and encoder together."
        )
    if hasattr(model, "n_features_in_") and X.shape[1] != int(model.n_features_in_):
        raise ValueError(
            f"Model expects {model.n_features_in_} features, but got {X.shape[1]}."
        )
    return model, X


def predict_company_status(company_name, industry, registration_no, gst_no):
    model, X = build_feature_vector(company_name, industry, registration_no, gst_no)
    prediction_raw = model.predict(X)[0]
    try:
        status_encoder = joblib.load(STATUS_LABEL_ENCODER_PATH)
        prediction = status_encoder.inverse_transform([int(prediction_raw)])[0]
    except Exception:
        prediction = prediction_raw
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)[0]
        return prediction, proba
    return prediction, None


if __name__ == "__main__":
    sample_company = {
        "company_name": "NovaTech Solutions Pvt Ltd",
        "industry": "IT",
        "registration_no": 10234,
        "gst_no": "27ABCDE1234F1Z5",
    }
    pred, proba = predict_company_status(
        sample_company["company_name"],
        sample_company["industry"],
        sample_company["registration_no"],
        sample_company["gst_no"],
    )
    print("Predicted Status:", pred)
    if proba is not None:
        print("Class Probabilities:", proba)
