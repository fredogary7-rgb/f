// Loader sur les boutons de soumission de formulaire
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form').forEach(function (form) {
        form.addEventListener('submit', function () {
            var btn = form.querySelector('button[type="submit"], input[type="submit"]');
            if (btn && !btn.classList.contains('btn-loading')) {
                btn.classList.add('btn-loading');
                btn.disabled = true;
            }
        });
    });
});
