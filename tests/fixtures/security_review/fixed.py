"""Controlled business-logic fixture: both positive and negative ownership paths."""


def get_invoice(invoices, invoice_id, caller):
    invoice = invoices[invoice_id]
    if invoice["owner"] != caller:
        return 403, None
    return 200, invoice["amount"]
