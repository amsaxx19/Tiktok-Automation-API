-- Add a unique constraint on (provider, provider_invoice_id) so that
-- duplicate Mayar webhook retries cannot insert duplicate transactions.

alter table public.payment_transactions
    add constraint payment_transactions_provider_invoice_uniq
        unique (provider, provider_invoice_id);
