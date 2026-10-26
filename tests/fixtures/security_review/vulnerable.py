"""Controlled business-logic fixture: object ownership is unchecked."""


def get_invoice(invoices, invoice_id, caller):
    invoice = invoices[invoice_id]
    return 200, invoice["amount"]
