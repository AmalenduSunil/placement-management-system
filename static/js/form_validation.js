function validateSignupForm() {
    let isValid = true;

    let registerNumber = document
        .getElementById("register_number")
        .value.trim()
        .toUpperCase();
    let name = document.getElementById("name").value.trim();
    let email = document.getElementById("email").value.trim();
    let password = document.getElementById("password").value.trim();

    document.getElementById("registerNumberError").innerText = "";
    document.getElementById("nameError").innerText = "";
    document.getElementById("emailError").innerText = "";
    document.getElementById("passwordError").innerText = "";

    const registerNumberPattern = /^CEC\d{2}[A-Z]{2}\d{3}$/;
    if (!registerNumberPattern.test(registerNumber)) {
        document.getElementById("registerNumberError").innerText =
            "Register number must be like CEC23CS027.";
        isValid = false;
    }

    if (name.length < 3) {
        document.getElementById("nameError").innerText =
            "Name must be at least 3 characters.";
        isValid = false;
    }

    if (!email.includes("@")) {
        document.getElementById("emailError").innerText =
            "Enter a valid email address.";
        isValid = false;
    }

    const strongPasswordPattern =
        /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$/;
    if (!strongPasswordPattern.test(password)) {
        document.getElementById("passwordError").innerText =
            "Use 8+ chars with uppercase, lowercase, number, and special character.";
        isValid = false;
    }

    return isValid;
}
