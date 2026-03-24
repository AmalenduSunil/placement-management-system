import pandas as pd
import random
import string

industries = [
    "IT Services", "Finance", "Real Estate", "Agriculture", "Education",
    "Manufacturing", "Healthcare", "Retail", "Telecom", "Energy"
]
statuses = ["Verified", "Pending", "Flagged"]

def random_gst(valid=True):
    if valid:
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=15))
    else:
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=random.randint(5,12)))

rows = []
counts = {"Verified": 667, "Pending": 667, "Flagged": 666}  # total = 2000
i = 0
for status, n in counts.items():
    for j in range(n):
        company = f"{status}_Company{i} Pvt Ltd"
        industry = random.choice(industries)
        reg_no = 10000 + i
        gst = random_gst(valid=(status=="Verified"))
        rows.append([company, industry, status, reg_no, gst])
        i += 1

df = pd.DataFrame(rows, columns=["Company","Industry","Status","RegistrationNo","GSTNo"])
df.to_csv("companies_large.csv", index=False)

print("Generated companies_large.csv with", len(df), "rows")
print(df['Status'].value_counts())