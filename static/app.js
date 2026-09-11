// Loader sur les boutons de soumission de formulaire
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            // Bouton qui a réellement déclenché la soumission
            var submitter = e.submitter;
            if (!submitter) {
                submitter = form.querySelector('button[type="submit"], input[type="submit"]');
            }
            if (submitter && !submitter.classList.contains('btn-loading')) {
                // Préserver name/value du bouton avant de le désactiver
                // (un bouton disabled n'envoie pas sa valeur)
                if (submitter.name) {
                    var hidden = document.createElement('input');
                    hidden.type = 'hidden';
                    hidden.name = submitter.name;
                    hidden.value = submitter.value;
                    form.appendChild(hidden);
                }
                submitter.classList.add('btn-loading');
                submitter.disabled = true;
            }
        });
    });
});
