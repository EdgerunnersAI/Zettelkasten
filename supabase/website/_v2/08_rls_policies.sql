-- DB v2 RLS policies. Custom schemas must also be exposed in Supabase API settings.

GRANT USAGE ON SCHEMA core, content, kg, rag, pipelines, billing TO anon, authenticated, service_role;
GRANT ALL ON ALL TABLES IN SCHEMA core, content, kg, rag, pipelines, billing TO service_role;
GRANT ALL ON ALL ROUTINES IN SCHEMA core, content, kg, rag, pipelines, billing TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA core, content, kg, rag, pipelines, billing TO service_role;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core, content, kg, rag, pipelines, billing TO authenticated;
GRANT EXECUTE ON ALL ROUTINES IN SCHEMA core, content, kg, rag, pipelines, billing TO authenticated;

ALTER TABLE core.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.workspace_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.usage_aggregates ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.soft_delete_queue ENABLE ROW LEVEL SECURITY;

ALTER TABLE content.embedding_model_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE content.canonical_zettels ENABLE ROW LEVEL SECURITY;
ALTER TABLE content.canonical_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE content.workspace_zettels ENABLE ROW LEVEL SECURITY;
ALTER TABLE content.workspace_chunk_membership ENABLE ROW LEVEL SECURITY;

ALTER TABLE kg.kg_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg.kg_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg.chunk_node_mentions ENABLE ROW LEVEL SECURITY;

ALTER TABLE rag.kastens ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.kasten_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.kasten_zettels ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.retrieval_signal_weights ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.retrieval_scorer_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.retrieval_scorer_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.retrieval_pipeline_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag.retrieval_pipeline_config_history ENABLE ROW LEVEL SECURITY;

ALTER TABLE pipelines.pipeline_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipelines.pipeline_run_items ENABLE ROW LEVEL SECURITY;

ALTER TABLE billing.pricing_billing_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_balances ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_payment_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_plan_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_refunds ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_disputes ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_plan_entitlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_entitlement_consumption ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing.pricing_webhook_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS canonical_zettels_service_all ON content.canonical_zettels;
DROP POLICY IF EXISTS canonical_chunks_service_all ON content.canonical_chunks;
DROP POLICY IF EXISTS embedding_model_versions_read ON content.embedding_model_versions;
DROP POLICY IF EXISTS embedding_model_versions_service_all ON content.embedding_model_versions;
DROP POLICY IF EXISTS profiles_self_read ON core.profiles;
DROP POLICY IF EXISTS profiles_service_all ON core.profiles;
DROP POLICY IF EXISTS workspaces_member_read ON core.workspaces;
DROP POLICY IF EXISTS workspaces_member_write ON core.workspaces;
DROP POLICY IF EXISTS workspaces_service_all ON core.workspaces;
DROP POLICY IF EXISTS workspace_members_member_read ON core.workspace_members;
DROP POLICY IF EXISTS workspace_members_service_all ON core.workspace_members;
DROP POLICY IF EXISTS workspace_zettels_member_select ON content.workspace_zettels;
DROP POLICY IF EXISTS workspace_zettels_member_insert ON content.workspace_zettels;
DROP POLICY IF EXISTS workspace_zettels_member_update ON content.workspace_zettels;
DROP POLICY IF EXISTS workspace_zettels_member_delete ON content.workspace_zettels;
DROP POLICY IF EXISTS workspace_zettels_service_all ON content.workspace_zettels;
DROP POLICY IF EXISTS workspace_chunks_member_select ON content.workspace_chunk_membership;
DROP POLICY IF EXISTS workspace_chunks_service_all ON content.workspace_chunk_membership;
DROP POLICY IF EXISTS kg_nodes_workspace_select ON kg.kg_nodes;
DROP POLICY IF EXISTS kg_nodes_workspace_write ON kg.kg_nodes;
DROP POLICY IF EXISTS kg_nodes_service_all ON kg.kg_nodes;
DROP POLICY IF EXISTS kg_edges_workspace_select ON kg.kg_edges;
DROP POLICY IF EXISTS kg_edges_workspace_write ON kg.kg_edges;
DROP POLICY IF EXISTS kg_edges_service_all ON kg.kg_edges;
DROP POLICY IF EXISTS chunk_node_mentions_service_all ON kg.chunk_node_mentions;
DROP POLICY IF EXISTS rag_registry_read ON rag.retrieval_scorer_registry;
DROP POLICY IF EXISTS rag_registry_versions_read ON rag.retrieval_scorer_version;
DROP POLICY IF EXISTS rag_pipeline_config_read ON rag.retrieval_pipeline_config;
DROP POLICY IF EXISTS usage_events_workspace_all ON core.usage_events;
DROP POLICY IF EXISTS usage_aggregates_workspace_select ON core.usage_aggregates;
DROP POLICY IF EXISTS quotas_workspace_select ON core.quotas;
DROP POLICY IF EXISTS kastens_workspace_all ON rag.kastens;
DROP POLICY IF EXISTS kasten_members_workspace_all ON rag.kasten_members;
DROP POLICY IF EXISTS chat_sessions_workspace_all ON rag.chat_sessions;
DROP POLICY IF EXISTS chat_messages_workspace_all ON rag.chat_messages;

-- Canonical content is service-role-only. Authenticated reads use content.search_chunks().
CREATE POLICY canonical_zettels_service_all ON content.canonical_zettels
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY canonical_chunks_service_all ON content.canonical_chunks
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY embedding_model_versions_read ON content.embedding_model_versions
    FOR SELECT TO authenticated USING (true);
CREATE POLICY embedding_model_versions_service_all ON content.embedding_model_versions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY profiles_self_read ON core.profiles
    FOR SELECT TO authenticated USING (id = auth.uid());
CREATE POLICY profiles_service_all ON core.profiles
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY workspaces_member_read ON core.workspaces
    FOR SELECT TO authenticated USING (id = ANY (core.jwt_workspace_ids()));
CREATE POLICY workspaces_member_write ON core.workspaces
    FOR UPDATE TO authenticated USING (id = ANY (core.jwt_workspace_ids())) WITH CHECK (id = ANY (core.jwt_workspace_ids()));
CREATE POLICY workspaces_service_all ON core.workspaces
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY workspace_members_member_read ON core.workspace_members
    FOR SELECT TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY workspace_members_service_all ON core.workspace_members
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY workspace_zettels_member_select ON content.workspace_zettels
    FOR SELECT TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY workspace_zettels_member_insert ON content.workspace_zettels
    FOR INSERT TO authenticated WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY workspace_zettels_member_update ON content.workspace_zettels
    FOR UPDATE TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids())) WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY workspace_zettels_member_delete ON content.workspace_zettels
    FOR DELETE TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY workspace_zettels_service_all ON content.workspace_zettels
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY workspace_chunks_member_select ON content.workspace_chunk_membership
    FOR SELECT TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY workspace_chunks_service_all ON content.workspace_chunk_membership
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY kg_nodes_workspace_select ON kg.kg_nodes
    FOR SELECT TO authenticated USING (workspace_id IS NULL OR workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY kg_nodes_workspace_write ON kg.kg_nodes
    FOR ALL TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids())) WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY kg_nodes_service_all ON kg.kg_nodes
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY kg_edges_workspace_select ON kg.kg_edges
    FOR SELECT TO authenticated USING (workspace_id IS NULL OR workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY kg_edges_workspace_write ON kg.kg_edges
    FOR ALL TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids())) WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY kg_edges_service_all ON kg.kg_edges
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY chunk_node_mentions_service_all ON kg.chunk_node_mentions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY rag_registry_read ON rag.retrieval_scorer_registry
    FOR SELECT TO authenticated USING (true);
CREATE POLICY rag_registry_versions_read ON rag.retrieval_scorer_version
    FOR SELECT TO authenticated USING (true);
CREATE POLICY rag_pipeline_config_read ON rag.retrieval_pipeline_config
    FOR SELECT TO authenticated USING (true);

CREATE POLICY usage_events_workspace_all ON core.usage_events
    FOR ALL TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids())) WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY usage_aggregates_workspace_select ON core.usage_aggregates
    FOR SELECT TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY quotas_workspace_select ON core.quotas
    FOR SELECT TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids()));

CREATE POLICY kastens_workspace_all ON rag.kastens
    FOR ALL TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids())) WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY kasten_members_workspace_all ON rag.kasten_members
    FOR ALL TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids())) WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY chat_sessions_workspace_all ON rag.chat_sessions
    FOR ALL TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids())) WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));
CREATE POLICY chat_messages_workspace_all ON rag.chat_messages
    FOR ALL TO authenticated USING (workspace_id = ANY (core.jwt_workspace_ids())) WITH CHECK (workspace_id = ANY (core.jwt_workspace_ids()));

-- Remaining operational tables are service-role only unless a narrower policy above exists.
DO $$
DECLARE
    rec record;
BEGIN
    FOR rec IN
        SELECT schemaname, tablename
          FROM pg_tables
         WHERE schemaname IN ('core', 'rag', 'pipelines', 'billing')
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = rec.schemaname
               AND tablename = rec.tablename
               AND policyname = 'service_role_all'
        ) THEN
            EXECUTE format(
                'CREATE POLICY service_role_all ON %I.%I FOR ALL TO service_role USING (true) WITH CHECK (true)',
                rec.schemaname,
                rec.tablename
            );
        END IF;
    END LOOP;
END
$$;
