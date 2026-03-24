import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import OneHotEncoder, LabelEncoder
from sklearn.metrics import classification_report
from imblearn.over_sampling import RandomOverSampler
import joblib

# 1. Load the data
data = pd.read_csv("companies_large.csv")

# 2. Inspect target distribution
print("Class distribution before resampling:\n", data['Status'].value_counts())

# 3. Feature engineering
data['GSTNo'] = data['GSTNo'].fillna("")
data['GST_Length'] = data['GSTNo'].apply(lambda x: len(str(x)))
data['Is_GST_Valid'] = data['GSTNo'].apply(lambda x: 1 if len(str(x)) == 15 else 0)
data['Has_Ltd'] = data['Company'].apply(lambda x: 1 if isinstance(x, str) and ('Ltd' in x or 'Pvt' in x) else 0)

# 4. Encode categorical features
encoder_industry = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
industry_encoded = encoder_industry.fit_transform(data[['Industry']])

# Encode target labels
encoder_status = LabelEncoder()
y = encoder_status.fit_transform(data['Status'])

# 5. Define features (X)
X = np.hstack([
    industry_encoded,
    data[['RegistrationNo', 'GST_Length', 'Is_GST_Valid', 'Has_Ltd']].values
])

# 6. Oversample minority classes
ros = RandomOverSampler(random_state=42)
X_resampled, y_resampled = ros.fit_resample(X, y)

print("Class distribution after resampling:\n", pd.Series(y_resampled).value_counts())

# 7. Train/test split
X_train, X_test, y_train, y_test = train_test_split(
    X_resampled, y_resampled, test_size=0.2, random_state=42
)

# 8. Train model
model = RandomForestClassifier(n_estimators=200, random_state=42)
model.fit(X_train, y_train)

# 9. Evaluate
y_pred = model.predict(X_test)
print("\nClassification Report:\n")
print(classification_report(y_test, y_pred))

# 10. Save model
joblib.dump(model, "company_model.pkl")
joblib.dump(encoder_industry, "industry_encoder.pkl")
joblib.dump(list(encoder_industry.get_feature_names_out(["Industry"])), "industry_feature_names.pkl")
joblib.dump(encoder_status, "status_label_encoder.pkl")
print("\nModel saved as company_model.pkl")
