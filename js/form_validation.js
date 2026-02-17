function validateSignupForm() {

    let isValid = true;

    let name = document.getElementById("name").value.trim();
    let email = document.getElementById("email").value.trim();
    let password = document.getElementById("password").value.trim();

    document.getElementById("nameError").innerText = "";
    document.getElementById("emailError").innerText = "";
    document.getElementById("passwordError").innerText = "";

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

    if (password.length < 6) {
        document.getElementById("passwordError").innerText =
            "Password must be at least 6 characters.";
        isValid = false;
    }

    return isValid;
}
