"""Where the optional account lives.

Both values are meant to be public. The publishable key identifies the project
and nothing more: it carries no privileges of its own, and every row it can
reach is decided by the row-level security policies in supabase/migrations.
Shipping it in the application is how Supabase is designed to be used - see
supabase/README.md for why those policies are the whole security model.
"""
from __future__ import annotations

PROJECT_URL = "https://lxwsbkofshgtznplwkvs.supabase.co"
PUBLISHABLE_KEY = "sb_publishable_2nf-jWZ026zK3tFs-01oDw_A0tahV19"

AUTH = PROJECT_URL + "/auth/v1"
REST = PROJECT_URL + "/rest/v1"
STORAGE = PROJECT_URL + "/storage/v1"
SIGNATURE_BUCKET = "signatures"
