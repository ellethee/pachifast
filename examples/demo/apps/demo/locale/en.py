"""Messages of demo in en: stable code -> text.

The code is the contract with the clients; keep the same codes and {fields} in every language
(`pachifast locale check`), and use the module name as the first part of each code.
"""

MESSAGES: dict[str, str] = {
    "demo.item_not_found": "Item {id} not found",
    "demo.file_not_found": "File not found",
    "demo.file_too_large": "The file is too large (maximum {max_mb} MB)",
    "demo.file_type_not_allowed": "File type not allowed (allowed: {types})",
    "demo.simulated_error": "Simulated error {status}",
    "demo.token_expired_once": "Token expired (simulated: retry after refreshing it)",
    "settings.billing.title": "Billing",
    "settings.billing.invoice_layout.label": "Invoice layout",
    "settings.billing.invoice_layout.options.classic": "Classic",
    "settings.billing.invoice_layout.options.compact": "Compact",
    "settings.billing.invoice_layout.options.detailed": "Detailed",
    "settings.billing.payment_terms_days.label": "Payment terms (days)",
}
