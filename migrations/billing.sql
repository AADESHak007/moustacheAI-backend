-- =========================================================================
-- Billing / credits schema for Razorpay integration.
-- Run this in the Supabase SQL editor.
--
-- The CreditsService falls back to in-memory state when these tables are
-- missing, so dev still works without running the migration — but you
-- want this in place for any environment where balances must survive
-- a restart.
-- =========================================================================

-- ---- Per-user credit balance --------------------------------------------
create table if not exists public.user_credits (
    user_id    uuid        primary key references auth.users (id) on delete cascade,
    balance    integer     not null default 0 check (balance >= 0),
    updated_at timestamptz not null default now()
);

alter table public.user_credits enable row level security;

-- Users can read their own balance; writes go through the service-role key.
drop policy if exists "user_credits_select_own" on public.user_credits;
create policy "user_credits_select_own"
    on public.user_credits for select
    using (auth.uid() = user_id);


-- ---- Payment ledger (Razorpay) ------------------------------------------
create table if not exists public.payments (
    id                   uuid        primary key default gen_random_uuid(),
    user_id              uuid        not null references auth.users (id) on delete cascade,
    plan_id              text        not null,
    amount_paise         integer     not null check (amount_paise > 0),
    currency             text        not null default 'INR',
    razorpay_order_id    text        not null unique,
    razorpay_payment_id  text,
    status               text        not null default 'paid'
                                     check (status in ('created','paid','failed','refunded')),
    credits_added        integer     not null default 0,
    created_at           timestamptz not null default now()
);

create index if not exists payments_user_id_created_at_idx
    on public.payments (user_id, created_at desc);

alter table public.payments enable row level security;

-- Users can see their own payment history.
drop policy if exists "payments_select_own" on public.payments;
create policy "payments_select_own"
    on public.payments for select
    using (auth.uid() = user_id);
