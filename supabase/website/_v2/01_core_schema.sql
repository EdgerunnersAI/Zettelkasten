-- DB v2 core schema: identity, workspaces, usage, quotas, and JWT helpers.

CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core.profiles (
    id                       uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name             text,
    email                    text,
    avatar_url               text,
    razorpay_subscriber_id   text UNIQUE,
    allowlist_status         text NOT NULL DEFAULT 'allowed'
                              CHECK (allowlist_status IN ('allowed', 'blocked', 'pending')),
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.workspaces (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_profile_id  uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    name              text NOT NULL,
    is_personal       boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_owner_personal
    ON core.workspaces(owner_profile_id)
    WHERE is_personal;

CREATE TABLE IF NOT EXISTS core.workspace_members (
    workspace_id  uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    profile_id    uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    role          text NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
    added_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, profile_id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_members_profile
    ON core.workspace_members(profile_id);

CREATE TABLE IF NOT EXISTS core.usage_events (
    id           bigserial,
    workspace_id uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    profile_id   uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    feature      text NOT NULL,
    unit         text NOT NULL,
    quantity     numeric NOT NULL DEFAULT 1,
    metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (occurred_at, id)
) PARTITION BY RANGE (occurred_at);

CREATE INDEX IF NOT EXISTS idx_usage_events_workspace_feature_time
    ON core.usage_events (workspace_id, feature, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_profile_time
    ON core.usage_events (profile_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS core.usage_aggregates (
    workspace_id    uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    profile_id      uuid NOT NULL REFERENCES core.profiles(id) ON DELETE CASCADE,
    feature         text NOT NULL,
    unit            text NOT NULL,
    period_start    timestamptz NOT NULL,
    quantity_total  numeric NOT NULL DEFAULT 0,
    events_count    bigint NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, feature, unit, period_start)
);

CREATE TABLE IF NOT EXISTS core.quotas (
    workspace_id  uuid NOT NULL REFERENCES core.workspaces(id) ON DELETE CASCADE,
    feature       text NOT NULL,
    unit          text NOT NULL,
    period_start  timestamptz NOT NULL,
    remaining     numeric NOT NULL,
    limit_total   numeric NOT NULL,
    PRIMARY KEY (workspace_id, feature, unit, period_start)
);

CREATE TABLE IF NOT EXISTS core.soft_delete_queue (
    id           bigserial PRIMARY KEY,
    table_name   text NOT NULL,
    row_id       uuid NOT NULL,
    enqueued_at  timestamptz NOT NULL DEFAULT now(),
    shred_after  timestamptz NOT NULL,
    shredded_at  timestamptz,
    UNIQUE (table_name, row_id)
);

CREATE INDEX IF NOT EXISTS idx_soft_delete_pending
    ON core.soft_delete_queue(shred_after)
    WHERE shredded_at IS NULL;

-- Audit B.1: parse JWT app_metadata.workspace_ids via jsonb_array_elements_text.
CREATE OR REPLACE FUNCTION core.jwt_workspace_ids() RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT COALESCE(
        ARRAY(
            SELECT jsonb_array_elements_text(
                COALESCE(auth.jwt() -> 'app_metadata' -> 'workspace_ids', '[]'::jsonb)
            )::uuid
        ),
        ARRAY[]::uuid[]
    );
$$;

GRANT EXECUTE ON FUNCTION core.jwt_workspace_ids() TO authenticated, anon;

-- Audit C.2: typed race-safe quota debit RPC.
CREATE OR REPLACE FUNCTION core.consume_quota(
    p_workspace_id uuid,
    p_feature text,
    p_unit text,
    p_period_start timestamptz
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    rem numeric;
BEGIN
    IF NOT (p_workspace_id = ANY (core.jwt_workspace_ids())) THEN
        RAISE EXCEPTION 'unauthorized' USING ERRCODE = '42501';
    END IF;

    UPDATE core.quotas
       SET remaining = remaining - 1
     WHERE workspace_id = p_workspace_id
       AND feature = p_feature
       AND unit = p_unit
       AND period_start = p_period_start
       AND remaining > 0
     RETURNING remaining INTO rem;

    RETURN FOUND;
END
$$;

GRANT EXECUTE ON FUNCTION core.consume_quota(uuid, text, text, timestamptz) TO authenticated;

CREATE OR REPLACE FUNCTION core.sync_workspace_ids_to_jwt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    affected_profile uuid := COALESCE(NEW.profile_id, OLD.profile_id);
    ids uuid[];
BEGIN
    SELECT array_agg(workspace_id)
      INTO ids
      FROM core.workspace_members
     WHERE profile_id = affected_profile;

    UPDATE auth.users
       SET raw_app_meta_data = jsonb_set(
           COALESCE(raw_app_meta_data, '{}'::jsonb),
           '{workspace_ids}',
           to_jsonb(COALESCE(ids, ARRAY[]::uuid[]))
       )
     WHERE id = affected_profile;

    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_workspace_members_jwt_sync ON core.workspace_members;
CREATE TRIGGER trg_workspace_members_jwt_sync
    AFTER INSERT OR DELETE OR UPDATE ON core.workspace_members
    FOR EACH ROW EXECUTE FUNCTION core.sync_workspace_ids_to_jwt();

CREATE OR REPLACE FUNCTION core.create_personal_workspace()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    ws_id uuid;
BEGIN
    INSERT INTO core.workspaces (owner_profile_id, name, is_personal)
    VALUES (NEW.id, 'Personal', true)
    ON CONFLICT DO NOTHING
    RETURNING id INTO ws_id;

    IF ws_id IS NULL THEN
        SELECT id INTO ws_id
          FROM core.workspaces
         WHERE owner_profile_id = NEW.id AND is_personal
         LIMIT 1;
    END IF;

    INSERT INTO core.workspace_members (workspace_id, profile_id, role)
    VALUES (ws_id, NEW.id, 'owner')
    ON CONFLICT DO NOTHING;

    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_profile_personal_workspace ON core.profiles;
CREATE TRIGGER trg_profile_personal_workspace
    AFTER INSERT ON core.profiles
    FOR EACH ROW EXECUTE FUNCTION core.create_personal_workspace();

CREATE OR REPLACE FUNCTION core.handle_new_auth_user()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    INSERT INTO core.profiles (id, email, display_name)
    VALUES (NEW.id, NEW.email, NEW.raw_user_meta_data ->> 'name')
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION core.handle_new_auth_user();

CREATE OR REPLACE FUNCTION core.enforce_allowlist()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM core.profiles
         WHERE id = NEW.owner_profile_id
           AND allowlist_status = 'allowed'
    ) THEN
        RAISE EXCEPTION 'profile not on allowlist' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_workspaces_allowlist_check ON core.workspaces;
CREATE TRIGGER trg_workspaces_allowlist_check
    BEFORE INSERT ON core.workspaces
    FOR EACH ROW EXECUTE FUNCTION core.enforce_allowlist();

