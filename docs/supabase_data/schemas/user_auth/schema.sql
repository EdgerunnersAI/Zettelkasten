-- ============================================================================
-- Supabase Auth Integration Schema
-- Run this in the Supabase SQL Editor AFTER the base kg_public schema.
-- Creates:
--   1. Trigger to auto-provision kg_users when new auth users sign up
--   2. "Naruto" default user for anonymous/unregistered Zettel uploads
-- ============================================================================

-- ── 1. Trigger: auto-create kg_users on Supabase Auth signup ────────────────

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = ''
AS $$
BEGIN
  INSERT INTO public.kg_users (render_user_id, display_name, email, avatar_url)
  VALUES (
    NEW.id::text,
    NEW.raw_user_meta_data ->> 'full_name',
    NEW.email,
    COALESCE(
      NEW.raw_user_meta_data ->> 'avatar_url',
      NEW.raw_user_meta_data ->> 'picture'
    )
  )
  ON CONFLICT (render_user_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    email = EXCLUDED.email,
    avatar_url = EXCLUDED.avatar_url,
    updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

COMMENT ON FUNCTION public.handle_new_user IS
  'Auto-provisions a kg_users row when a new Supabase Auth user signs up (Google OAuth, etc.)';


-- ── 2. Default user "Naruto" ────────────────────────────────────────────────
-- All Zettels without an authenticated user are assigned to Naruto.
-- This replaces the old "default-web-user" placeholder.

INSERT INTO public.kg_users (render_user_id, display_name, email, avatar_url)
VALUES (
  'naruto',
  'Naruto',
  'naruto@zettelkasten.local',
  NULL
)
ON CONFLICT (render_user_id) DO UPDATE SET
  display_name = 'Naruto',
  email = 'naruto@zettelkasten.local',
  updated_at = now();


-- ── 3. Migrate existing Zettels from default-web-user to Naruto ─────────────
-- Move all nodes and links owned by the old default user to the Naruto user.

DO $$
DECLARE
  old_user_id UUID;
  naruto_id UUID;
  node_count INT;
  link_count INT;
BEGIN
  SELECT id INTO old_user_id FROM public.kg_users
    WHERE render_user_id = 'default-web-user' LIMIT 1;
  SELECT id INTO naruto_id FROM public.kg_users
    WHERE render_user_id = 'naruto' LIMIT 1;

  IF old_user_id IS NOT NULL AND naruto_id IS NOT NULL AND old_user_id != naruto_id THEN
    SELECT COUNT(*) INTO link_count FROM public.kg_links WHERE user_id = old_user_id;
    SELECT COUNT(*) INTO node_count FROM public.kg_nodes WHERE user_id = old_user_id;

    -- Delete links first (composite FK: user_id+node_id)
    DELETE FROM public.kg_links WHERE user_id = old_user_id;
    -- Move nodes to Naruto
    UPDATE public.kg_nodes SET user_id = naruto_id WHERE user_id = old_user_id;
    -- Deactivate old user
    UPDATE public.kg_users SET is_active = false WHERE id = old_user_id;

    RAISE NOTICE 'Migrated % nodes to Naruto (% old links removed)', node_count, link_count;
  END IF;
END;
$$;
