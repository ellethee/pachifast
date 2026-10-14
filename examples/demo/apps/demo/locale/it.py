"""Messages of demo in it: stable code -> text.

The code is the contract with the clients; keep the same codes and {fields} in every language
(`pachifast locale check`), and use the module name as the first part of each code.
"""

MESSAGES: dict[str, str] = {
    "demo.item_not_found": "Elemento {id} non trovato",
    "demo.file_not_found": "File non trovato",
    "demo.file_too_large": "Il file è troppo grande (massimo {max_mb} MB)",
    "demo.file_type_not_allowed": "Tipo di file non consentito (consentiti: {types})",
    "demo.simulated_error": "Errore simulato {status}",
    "demo.token_expired_once": "Token scaduto (simulato: riprova dopo averlo rinnovato)",
    "settings.billing.title": "Fatturazione",
    "settings.billing.invoice_layout.label": "Layout fattura",
    "settings.billing.invoice_layout.options.classic": "Classico",
    "settings.billing.invoice_layout.options.compact": "Compatto",
    "settings.billing.invoice_layout.options.detailed": "Dettagliato",
    "settings.billing.payment_terms_days.label": "Termini di pagamento (giorni)",
}
