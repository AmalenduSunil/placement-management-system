"""
Fraud detection module (SQLAlchemy-friendly, no Firebase).

Design goals:
- No hard dependency on optional libraries or network availability.
- Web/API checks are best-effort with short timeouts and graceful fallbacks.
- Duplicate checks can use SQLAlchemy session + Company model when provided.
"""

from __future__ import annotations

import os
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse


DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "guerrillamail.com",
    "tempmail.com",
    "throwaway.email",
    "yopmail.com",
    "sharklasers.com",
    "dispostable.com",
    "mailnesia.com",
    "maildrop.cc",
    "discard.email",
    "trashmail.com",
    "trashmail.me",
    "trashmail.net",
    "temp-mail.org",
    "fakeinbox.com",
    "tempr.email",
    "emailondeck.com",
    "getnada.com",
    "10minutemail.com",
    "minutemail.com",
}

PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "aol.com",
    "icloud.com",
    "mail.com",
    "protonmail.com",
    "zoho.com",
    "gmx.com",
    "yandex.com",
    "live.com",
    "msn.com",
    "rediffmail.com",
    "yahoo.co.in",
    "yahoo.co.uk",
}

GST_STATE_CODES = {
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "20",
    "21",
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "28",
    "29",
    "30",
    "31",
    "32",
    "33",
    "34",
    "35",
    "36",
    "37",
    "38",
}

DNS_BLACKLISTS = [
    "zen.spamhaus.org",
    "bl.spamcop.net",
    "b.barracudacentral.org",
    "dnsbl.sorbs.net",
]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_email_format(email: str) -> Dict[str, Any]:
    email = (email or "").strip()
    if not email:
        return {"valid": False, "reason": "Empty email"}
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
    valid = bool(re.match(pattern, email))
    return {"valid": valid, "reason": "Valid format" if valid else "Invalid email format"}


def validate_phone_format(phone: str) -> Dict[str, Any]:
    phone = (phone or "").strip()
    if not phone:
        return {"valid": False, "country": None, "reason": "Empty phone number"}
    try:
        import phonenumbers  # type: ignore

        parsed = phonenumbers.parse(phone, "IN")
        valid = phonenumbers.is_valid_number(parsed)
        country = phonenumbers.region_code_for_number(parsed)
        return {
            "valid": bool(valid),
            "country": country,
            "reason": "Valid phone number" if valid else "Invalid phone number",
        }
    except Exception as exc:
        return {"valid": False, "country": None, "reason": f"Phone validation error: {exc}"}


def validate_website_format(website: str) -> Dict[str, Any]:
    website = (website or "").strip()
    if not website:
        return {"valid": False, "domain": None, "reason": "No website provided"}
    try:
        parsed = urlparse(website)
        has_scheme = parsed.scheme in ("http", "https")
        has_netloc = bool(parsed.netloc)
        valid = bool(has_scheme and has_netloc)
        domain = parsed.netloc if valid else None
        return {
            "valid": valid,
            "domain": domain,
            "reason": "Valid URL" if valid else "Invalid URL format (must start with http:// or https://)",
        }
    except Exception as exc:
        return {"valid": False, "domain": None, "reason": f"URL parsing failed: {exc}"}


def validate_gst_format(gst_number: str) -> Dict[str, Any]:
    gst_number = (gst_number or "").strip().upper()
    if not gst_number:
        return {"valid": False, "reason": "No GST number provided"}
    if len(gst_number) != 15:
        return {"valid": False, "reason": f"GST must be 15 characters (got {len(gst_number)})"}
    pattern = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
    if not re.match(pattern, gst_number):
        return {"valid": False, "reason": "Invalid GST format"}
    state_code = gst_number[:2]
    if state_code not in GST_STATE_CODES:
        return {"valid": False, "reason": f"Invalid state code: {state_code}"}
    return {"valid": True, "reason": "Valid GST format"}


def check_disposable_email(email: str) -> Dict[str, Any]:
    email = (email or "").strip()
    if not email or "@" not in email:
        return {"is_disposable": False, "reason": "No email"}
    domain = email.split("@", 1)[1].lower()
    is_disposable = domain in DISPOSABLE_DOMAINS
    return {
        "is_disposable": is_disposable,
        "domain": domain,
        "reason": "Disposable email domain detected" if is_disposable else "Not a disposable domain",
    }


def check_public_email(email: str) -> Dict[str, Any]:
    email = (email or "").strip()
    if not email or "@" not in email:
        return {"is_public": False, "reason": "No email"}
    domain = email.split("@", 1)[1].lower()
    is_public = domain in PUBLIC_EMAIL_DOMAINS
    return {
        "is_public": is_public,
        "domain": domain,
        "reason": "Public/free email domain (not corporate)" if is_public else "Corporate email domain",
    }


def check_mx_records(email: str) -> Dict[str, Any]:
    email = (email or "").strip()
    if not email or "@" not in email:
        return {"valid": False, "mx_records": [], "reason": "No email"}

    domain = email.split("@", 1)[1]
    try:
        import dns.resolver  # type: ignore

        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        mx_records = [str(r.exchange) for r in answers]
        return {
            "valid": len(mx_records) > 0,
            "mx_records": mx_records[:3],
            "reason": f"Found {len(mx_records)} MX record(s)" if mx_records else "No MX records",
        }
    except Exception as exc:
        return {
            "valid": True,
            "mx_records": [],
            "reason": f"MX check skipped: {exc}",
        }


def _extract_domain(website: str) -> Optional[str]:
    website = (website or "").strip()
    if not website:
        return None
    parsed = urlparse(website)
    host = (parsed.netloc or parsed.path or "").strip().lower()
    host = host.split("/", 1)[0].split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def check_domain_age(website: str) -> Dict[str, Any]:
    domain = _extract_domain(website)
    if not domain:
        return {"age_days": None, "is_new": True, "reason": "No website provided"}
    try:
        import whois  # type: ignore

        record = whois.whois(domain)
        creation_date = getattr(record, "creation_date", None)
        if isinstance(creation_date, list):
            creation_date = creation_date[0] if creation_date else None
        if not creation_date:
            return {"age_days": None, "is_new": False, "reason": "Could not determine domain creation date"}

        if getattr(creation_date, "tzinfo", None) is not None:
            creation_date = creation_date.replace(tzinfo=None)
        age_days = (datetime.now() - creation_date).days
        is_new = age_days < 90
        return {
            "age_days": age_days,
            "is_new": is_new,
            "creation_date": str(creation_date),
            "reason": f"Domain is {age_days} days old" + (" (< 90 days — suspicious)" if is_new else ""),
        }
    except Exception as exc:
        return {"age_days": None, "is_new": False, "reason": f"WHOIS lookup failed: {exc}"}


def check_ssl_certificate(website: str) -> Dict[str, Any]:
    domain = _extract_domain(website)
    if not domain:
        return {"valid": False, "reason": "No website provided"}
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=3) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                is_valid = not_after > datetime.now()
                return {
                    "valid": bool(is_valid),
                    "expires": str(not_after),
                    "reason": "Valid SSL certificate" if is_valid else "SSL certificate expired",
                }
    except Exception as exc:
        return {"valid": False, "reason": f"SSL check failed: {exc}"}


def check_domain_blacklist(website: str) -> Dict[str, Any]:
    domain = _extract_domain(website)
    if not domain:
        return {"blacklisted": False, "reason": "No website provided"}
    try:
        try:
            ip = socket.gethostbyname(domain)
        except socket.gaierror:
            return {"blacklisted": False, "reason": "Could not resolve domain to IP"}

        reversed_ip = ".".join(reversed(ip.split(".")))
        blacklisted_on = []
        for bl in DNS_BLACKLISTS:
            try:
                query = f"{reversed_ip}.{bl}"
                socket.setdefaulttimeout(2)
                socket.gethostbyname(query)
                blacklisted_on.append(bl)
            except socket.gaierror:
                continue
            except Exception:
                continue
        is_blacklisted = len(blacklisted_on) > 0
        return {
            "blacklisted": is_blacklisted,
            "blacklists": blacklisted_on,
            "reason": f"Listed on: {', '.join(blacklisted_on)}" if is_blacklisted else "Not blacklisted",
        }
    except Exception as exc:
        return {"blacklisted": False, "reason": f"Blacklist check error: {exc}"}


def check_phone_country_match(phone: str, address: str) -> Dict[str, Any]:
    phone = (phone or "").strip()
    if not phone:
        return {"match": True, "reason": "No phone number to check"}
    try:
        import phonenumbers  # type: ignore

        parsed = phonenumbers.parse(phone, "IN")
        phone_country = phonenumbers.region_code_for_number(parsed)

        address_lower = (address or "").lower()
        india_keywords = [
            "india",
            "mumbai",
            "delhi",
            "bangalore",
            "chennai",
            "hyderabad",
            "kolkata",
            "pune",
            "ahmedabad",
            "jaipur",
            "goa",
            "kerala",
            "tamil",
            "karnataka",
            "maharashtra",
            "gujarat",
            "rajasthan",
            "uttar pradesh",
            "madhya pradesh",
        ]
        is_india_address = any(kw in address_lower for kw in india_keywords) if address else True
        if is_india_address and phone_country and phone_country != "IN":
            return {
                "match": False,
                "phone_country": phone_country,
                "reason": f"Phone country ({phone_country}) does not match India address",
            }
        return {"match": True, "phone_country": phone_country, "reason": "Phone country matches address"}
    except Exception as exc:
        return {"match": True, "reason": f"Country match check skipped: {exc}"}


def _requests_get(url: str, timeout_s: float = 4.0):
    try:
        import requests  # type: ignore

        return requests.get(url, timeout=timeout_s)
    except Exception:
        return None


def check_hunter_email(email: str) -> Dict[str, Any]:
    api_key = (os.getenv("HUNTER_API_KEY") or "").strip()
    email = (email or "").strip()
    if not api_key:
        return {"skipped": True, "reason": "HUNTER_API_KEY not configured"}
    if not email:
        return {"skipped": True, "reason": "No email provided"}

    url = f"https://api.hunter.io/v2/email-verifier?email={email}&api_key={api_key}"
    response = _requests_get(url, timeout_s=4.0)
    if response is None:
        return {"skipped": True, "reason": "Hunter API request failed"}
    if getattr(response, "status_code", None) != 200:
        return {"skipped": True, "reason": f"Hunter API error: {getattr(response, 'status_code', 'unknown')}"}

    try:
        data = response.json().get("data", {})
    except Exception:
        return {"skipped": True, "reason": "Hunter API response parse failed"}

    status = data.get("status")
    score = data.get("score", 0)
    is_invalid = status == "invalid" or (status == "unknown" and _safe_int(score) < 50)
    return {
        "skipped": False,
        "is_invalid": bool(is_invalid),
        "status": status,
        "score": score,
        "reason": f"Hunter: {status} (Score: {score})",
    }


def check_abstract_phone(phone: str) -> Dict[str, Any]:
    api_key = (os.getenv("ABSTRACT_PHONE_API_KEY") or "").strip()
    phone = (phone or "").strip()
    if not api_key:
        return {"skipped": True, "reason": "ABSTRACT_PHONE_API_KEY not configured"}
    if not phone:
        return {"skipped": True, "reason": "No phone provided"}

    url = f"https://phonevalidation.abstractapi.com/v1/?api_key={api_key}&phone={phone}"
    response = _requests_get(url, timeout_s=4.0)
    if response is None:
        return {"skipped": True, "reason": "Abstract API request failed"}
    if getattr(response, "status_code", None) != 200:
        return {"skipped": True, "reason": f"Abstract API error: {getattr(response, 'status_code', 'unknown')}"}

    try:
        data = response.json()
    except Exception:
        return {"skipped": True, "reason": "Abstract API response parse failed"}

    is_valid = data.get("valid")
    line_type = data.get("type")
    return {
        "skipped": False,
        "is_invalid": bool(is_valid is False),
        "type": line_type,
        "reason": f"Abstract: {'Valid' if is_valid else 'Invalid'} ({line_type})",
    }


def check_database_duplicates(session, CompanyModel, company_data: Dict[str, Any]) -> Dict[str, Any]:
    results: Dict[str, Any] = {"is_duplicate": False, "matches": []}
    if session is None or CompanyModel is None:
        return results

    name = (company_data.get("company_name") or "").strip()
    email = (company_data.get("contact_email") or "").strip()
    reg_num = (company_data.get("registration_number") or "").strip()

    try:
        if name:
            existing = session.query(CompanyModel).filter(CompanyModel.company_name.ilike(name)).first()
            if existing:
                results["is_duplicate"] = True
                results["matches"].append(f"Company name already exists: {existing.company_name}")

        if email:
            existing = session.query(CompanyModel).filter(CompanyModel.contact_email == email).first()
            if existing:
                results["is_duplicate"] = True
                results["matches"].append(f"Email already used by: {existing.company_name}")

        if reg_num:
            existing = session.query(CompanyModel).filter(CompanyModel.registration_number == reg_num).first()
            if existing:
                results["is_duplicate"] = True
                results["matches"].append(f"Registration number used by: {existing.company_name}")
    except Exception as exc:
        results["error"] = str(exc)

    return results


def calculate_fraud_score(checks: Dict[str, Any]) -> Dict[str, Any]:
    score = 0
    breakdown = []

    def add(check: str, failed: bool, points: int, pass_label: str = "PASS", fail_label: str = "FAIL"):
        nonlocal score
        if failed:
            score += points
            breakdown.append({"check": check, "points": points, "status": fail_label})
        else:
            breakdown.append({"check": check, "points": 0, "status": pass_label})

    add("Company Name Present", not bool(checks.get("company_name_present", True)), 20)
    add("Disposable Email", bool(checks.get("disposable_email", {}).get("is_disposable")), 40)

    domain_age = checks.get("domain_age", {})
    add("Domain Age (< 90 days)", bool(domain_age.get("is_new")), 30)

    add("Domain Blacklist", bool(checks.get("domain_blacklist", {}).get("blacklisted")), 50)

    add("Phone-Country Match", not bool(checks.get("phone_country_match", {}).get("match", True)), 20)

    website_fmt = checks.get("website_format", {})
    add("Website Present", not bool(website_fmt.get("valid")), 10)

    gst = checks.get("gst_format", {})
    add("GST Validation", (gst.get("reason") != "No GST number provided") and (not bool(gst.get("valid"))), 15)

    add("Corporate Email", bool(checks.get("public_email", {}).get("is_public")), 10)

    ssl_cert = checks.get("ssl_certificate", {})
    add("SSL Certificate", bool(website_fmt.get("valid")) and (not bool(ssl_cert.get("valid"))), 10)

    mx = checks.get("mx_records", {}) or {}
    mx_valid = bool(mx.get("valid"))
    mx_reason = str(mx.get("reason") or "")
    if (not mx_valid) and ("failed" not in mx_reason.lower()):
        add("MX Records", True, 15)
    else:
        add("MX Records", False, 15)

    hunter = checks.get("hunter_email", {})
    if not hunter.get("skipped"):
        add("Hunter API: Valid Email", bool(hunter.get("is_invalid")), 40)

    abstract = checks.get("abstract_phone", {})
    if not abstract.get("skipped"):
        add("Abstract API: Valid Phone", bool(abstract.get("is_invalid")), 25)

    normalized_score = min(100.0, float(score))
    if normalized_score >= 70:
        classification = "fraud"
    elif normalized_score >= 30:
        classification = "suspicious"
    else:
        classification = "legitimate"

    return {
        "raw_score": score,
        "normalized_score": normalized_score,
        "classification": classification,
        "breakdown": breakdown,
    }


def _run_ml_model(company_data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from sklearn.ensemble import IsolationForest  # type: ignore
        from sklearn.preprocessing import StandardScaler  # type: ignore
        import numpy as np  # type: ignore

        sample_data = [
            [1.0, 1, 1, 1, 5, 0],
            [1.2, 1, 1, 1, 10, 0],
            [0.8, 1, 1, 1, 2, 0],
            [1.1, 1, 1, 1, 8, 0],
            [0.9, 1, 1, 1, 3, 0],
            [5.0, 0, 0, 0, 0, 1],
            [0.2, 1, 0, 1, 0, 1],
            [3.0, 1, 0, 0, 1, 1],
            [1.0, 0, 0, 1, 0, 0],
        ]

        try:
            salary = float(company_data.get("salary_package", 1) or 1)
        except (TypeError, ValueError):
            salary = 1.0
        salary = max(1.0, salary)
        salary_anomaly = salary / 10.0 if salary > 0 else 0.0

        website = (company_data.get("website") or "").strip()
        website_validity = 1 if website and website.startswith("http") else 0

        email = (company_data.get("contact_email") or "").lower()
        domain_email_usage = 1 if email and (not any(d in email for d in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com"))) else 0

        reg_status = 1 if (company_data.get("registration_number") or "").strip() else 0
        history = _safe_int(company_data.get("previous_history"), 0)
        unrealistic = 1 if (salary > 15 and (reg_status == 0 or domain_email_usage == 0)) else 0

        current_features = [salary_anomaly, website_validity, domain_email_usage, reg_status, history, unrealistic]
        all_data = sample_data + [current_features]

        scaler = StandardScaler()
        scaled_data = scaler.fit_transform(all_data)

        iso_forest = IsolationForest(contamination=0.2, random_state=42)
        _ = iso_forest.fit_predict(scaled_data)
        anomaly_scores = iso_forest.decision_function(scaled_data)

        current_score = float(anomaly_scores[-1])
        risk_score_pct = round(max(0.0, min(100.0, (0.5 - current_score) * 100.0)), 2)
        return {"risk_score_pct": risk_score_pct, "anomaly_score": current_score}
    except Exception:
        return {"risk_score_pct": 50.0, "anomaly_score": 0.0}


def run_full_analysis(
    company_name=None,
    contact_email=None,
    gst_number=None,
    website=None,
    phone_number=None,
    company_data=None,
    session=None,
    CompanyModel=None,
) -> Dict[str, Any]:
    if company_data is None:
        company_data = {
            "company_name": company_name,
            "contact_email": contact_email,
            "gst_number": gst_number,
            "website": website,
            "contact_phone": phone_number,  # IMPORTANT
            "address": ""
        }
    email = (company_data.get("contact_email") or "").strip()
    phone = (company_data.get("contact_phone") or "").strip()
    website = (company_data.get("website") or "").strip()
    gst = (company_data.get("gst_number") or "").strip()
    address = (company_data.get("address") or "").strip()

    layer1 = {
        "email_format": validate_email_format(email),
        "phone_format": validate_phone_format(phone),
        "website_format": validate_website_format(website),
        "gst_format": validate_gst_format(gst),
    }

    layer2 = check_database_duplicates(session, CompanyModel, company_data) if CompanyModel is not None else {}

    layer3 = {
        "disposable_email": check_disposable_email(email),
        "public_email": check_public_email(email),
        "mx_records": check_mx_records(email),
        "domain_age": check_domain_age(website),
        "ssl_certificate": check_ssl_certificate(website),
        "domain_blacklist": check_domain_blacklist(website),
        "phone_country_match": check_phone_country_match(phone, address),
    }

    layer4 = {
        "hunter_email": check_hunter_email(email),
        "abstract_phone": check_abstract_phone(phone),
    }

    all_checks = {
        "company_name_present": bool((company_data.get("company_name") or "").strip()),
        **layer1,
        **layer3,
        **layer4,
    }
    scoring = calculate_fraud_score(all_checks)
    ml_result = _run_ml_model(company_data)

    combined_score = round(scoring["normalized_score"] * 0.6 + float(ml_result["risk_score_pct"]) * 0.4, 2)
    if combined_score >= 70:
        classification = "fraud"
    elif combined_score >= 30:
        classification = "suspicious"
    else:
        classification = "legitimate"

    reasons = [
        f"{item['check']} (+{item['points']}pts)"
        for item in (scoring.get("breakdown") or [])
        if item.get("status") == "FAIL"
    ]

    return {
        "classification": classification,
        "risk_score_pct": combined_score,
        "is_fraud": classification == "fraud",
        "anomaly_score": float(ml_result.get("anomaly_score", 0.0) or 0.0),
        "reasons": "; ".join(reasons) if reasons else "All checks passed",
        "web_score": float(scoring.get("normalized_score") or 0.0),
        "ml_score": float(ml_result.get("risk_score_pct") or 0.0),
        "layer1_format": layer1,
        "layer2_database": layer2,
        "layer3_web": layer3,
        "layer4_api": layer4,
        "scoring_breakdown": scoring.get("breakdown") or [],
    }


def run_quick_analysis(company_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fast, synchronous analysis meant for instant UI feedback.
    Avoids network/API calls and database lookups; uses format checks + ML heuristic only.
    """
    if company_data is None:
        company_data = {}

    email = (company_data.get("contact_email") or "").strip()
    phone = (company_data.get("contact_phone") or "").strip()
    website = (company_data.get("website") or "").strip()
    gst = (company_data.get("gst_number") or "").strip()

    layer1 = {
        "email_format": validate_email_format(email),
        "phone_format": validate_phone_format(phone),
        "website_format": validate_website_format(website),
        "gst_format": validate_gst_format(gst),
    }

    all_checks = {
        "company_name_present": bool((company_data.get("company_name") or "").strip()),
        "disposable_email": check_disposable_email(email),
        "public_email": check_public_email(email),
        **layer1,
    }
    scoring = calculate_fraud_score(all_checks)
    # Keep this path very fast: no network calls and no heavy ML imports.
    combined_score = float(scoring["normalized_score"])
    if combined_score >= 70:
        classification = "fraud"
    elif combined_score >= 30:
        classification = "suspicious"
    else:
        classification = "legitimate"

    reasons = [
        f"{item['check']} (+{item['points']}pts)"
        for item in (scoring.get("breakdown") or [])
        if item.get("status") == "FAIL"
    ]

    return {
        "analysis_stage": "quick",
        "classification": classification,
        "risk_score_pct": combined_score,
        "is_fraud": classification == "fraud",
        "anomaly_score": 0.0,
        "reasons": "; ".join(reasons) if reasons else "Quick checks passed (format-only). Full checks running.",
        "web_score": float(scoring.get("normalized_score") or 0.0),
        "ml_score": 0.0,
        "layer1_format": layer1,
        "layer2_database": {},
        "layer3_web": {},
        "layer4_api": {},
        "scoring_breakdown": scoring.get("breakdown") or [],
    }
