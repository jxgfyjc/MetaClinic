-- 0003_style_profiles: 医师经验画像（风格学习）——导入批次/病例/经验条目/版本/授权/接诊选用，以及方案的参考快照列。
-- Statements are separated by `-- ;;` lines. Never edit after it has been applied: add 0004_*.sql instead.
-- ;;
ALTER TABLE treatment_plans ADD COLUMN style_json JSON NOT NULL DEFAULT '{}'
-- ;;
CREATE TABLE style_profiles (
	id VARCHAR(40) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	subject_name VARCHAR(64) NOT NULL, 
	subject_title VARCHAR(64) NOT NULL, 
	subject_dept VARCHAR(64) NOT NULL, 
	intro TEXT NOT NULL, 
	owner_user_id VARCHAR(40), 
	reviewer_id VARCHAR(40), 
	proxy_created BOOLEAN NOT NULL, 
	created_by VARCHAR(40) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	active_version_id VARCHAR(40), 
	disabled_reason VARCHAR(200) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	row_version INTEGER NOT NULL, 
	CONSTRAINT pk_style_profiles PRIMARY KEY (id), 
	CONSTRAINT ck_style_profiles_kind CHECK (kind IN ('master', 'personal')), 
	CONSTRAINT ck_style_profiles_status CHECK (status IN ('active', 'disabled')), 
	CONSTRAINT fk_style_profiles_owner_user_id_users FOREIGN KEY(owner_user_id) REFERENCES users (id), 
	CONSTRAINT fk_style_profiles_reviewer_id_users FOREIGN KEY(reviewer_id) REFERENCES users (id)
)
-- ;;
CREATE INDEX ix_style_profiles_owner ON style_profiles (owner_user_id, kind)
-- ;;
CREATE TABLE encounter_style_choices (
	id INTEGER NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	profile_id VARCHAR(40) NOT NULL, 
	chosen_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_encounter_style_choices PRIMARY KEY (id), 
	CONSTRAINT encounter_profile UNIQUE (encounter_id, profile_id), 
	CONSTRAINT fk_encounter_style_choices_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id) ON DELETE CASCADE, 
	CONSTRAINT fk_encounter_style_choices_profile_id_style_profiles FOREIGN KEY(profile_id) REFERENCES style_profiles (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_encounter_style_choices_encounter_id ON encounter_style_choices (encounter_id)
-- ;;
CREATE TABLE style_authorizations (
	id VARCHAR(40) NOT NULL, 
	profile_id VARCHAR(40) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	basis_type VARCHAR(12) NOT NULL, 
	basis_text VARCHAR(400) NOT NULL, 
	authorizer_name VARCHAR(64) NOT NULL, 
	scope VARCHAR(12) NOT NULL, 
	scope_dept VARCHAR(64) NOT NULL, 
	valid_until DATETIME, 
	declared_by VARCHAR(40) NOT NULL, 
	declared_by_role VARCHAR(12) NOT NULL, 
	approved_by VARCHAR(40), 
	approved_at DATETIME, 
	revoked_by VARCHAR(40), 
	revoked_at DATETIME, 
	revoke_reason VARCHAR(300) NOT NULL, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_style_authorizations PRIMARY KEY (id), 
	CONSTRAINT ck_style_authorizations_status CHECK (status IN ('pending', 'approved', 'revoked')), 
	CONSTRAINT fk_style_authorizations_profile_id_style_profiles FOREIGN KEY(profile_id) REFERENCES style_profiles (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_style_authorizations_profile ON style_authorizations (profile_id, status)
-- ;;
CREATE TABLE style_imports (
	id VARCHAR(40) NOT NULL, 
	profile_id VARCHAR(40) NOT NULL, 
	label VARCHAR(120) NOT NULL, 
	status VARCHAR(14) NOT NULL, 
	stats_json JSON NOT NULL, 
	config_json JSON NOT NULL, 
	extract_mode VARCHAR(10) NOT NULL, 
	ext_ack_by VARCHAR(40), 
	ext_ack_at DATETIME, 
	draft_version_id VARCHAR(40), 
	job_id VARCHAR(40), 
	error_code VARCHAR(40) NOT NULL, 
	error_detail TEXT NOT NULL, 
	sources_purged_at DATETIME, 
	created_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_style_imports PRIMARY KEY (id), 
	CONSTRAINT ck_style_imports_status CHECK (status IN ('parsing', 'needs_review', 'extracting', 'extracted', 'failed', 'cancelled')), 
	CONSTRAINT fk_style_imports_profile_id_style_profiles FOREIGN KEY(profile_id) REFERENCES style_profiles (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_style_imports_profile ON style_imports (profile_id, created_at)
-- ;;
CREATE TABLE style_versions (
	id VARCHAR(40) NOT NULL, 
	profile_id VARCHAR(40) NOT NULL, 
	version_no INTEGER NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	parent_version_id VARCHAR(40), 
	note VARCHAR(400) NOT NULL, 
	stats_json JSON NOT NULL, 
	snapshot_hash VARCHAR(64) NOT NULL, 
	created_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	activated_by VARCHAR(40), 
	activated_at DATETIME, 
	retired_at DATETIME, 
	CONSTRAINT pk_style_versions PRIMARY KEY (id), 
	CONSTRAINT ck_style_versions_status CHECK (status IN ('draft', 'active', 'retired')), 
	CONSTRAINT profile_version UNIQUE (profile_id, version_no), 
	CONSTRAINT fk_style_versions_profile_id_style_profiles FOREIGN KEY(profile_id) REFERENCES style_profiles (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_style_versions_profile_status ON style_versions (profile_id, status)
-- ;;
CREATE TABLE style_cases (
	id VARCHAR(40) NOT NULL, 
	profile_id VARCHAR(40) NOT NULL, 
	import_id VARCHAR(40) NOT NULL, 
	source_id VARCHAR(40), 
	seq INTEGER NOT NULL, 
	kind VARCHAR(6) NOT NULL, 
	origin VARCHAR(10) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	source_label VARCHAR(160) NOT NULL, 
	text TEXT NOT NULL, 
	redaction_json JSON NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	dup_of_id VARCHAR(40), 
	dup_score FLOAT NOT NULL, 
	anomaly_json JSON NOT NULL, 
	herbs_json JSON NOT NULL, 
	structured_json JSON NOT NULL, 
	struct_status VARCHAR(10) NOT NULL, 
	struct_model VARCHAR(64) NOT NULL, 
	struct_error VARCHAR(300) NOT NULL, 
	visit_date VARCHAR(10) NOT NULL, 
	link_key VARCHAR(80) NOT NULL, 
	reviewed_by VARCHAR(40), 
	reviewed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	row_version INTEGER NOT NULL, 
	CONSTRAINT pk_style_cases PRIMARY KEY (id), 
	CONSTRAINT ck_style_cases_status CHECK (status IN ('pending_review', 'confirmed', 'excluded')), 
	CONSTRAINT fk_style_cases_profile_id_style_profiles FOREIGN KEY(profile_id) REFERENCES style_profiles (id) ON DELETE CASCADE, 
	CONSTRAINT fk_style_cases_import_id_style_imports FOREIGN KEY(import_id) REFERENCES style_imports (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_style_cases_import_status ON style_cases (import_id, status)
-- ;;
CREATE INDEX ix_style_cases_profile_hash ON style_cases (profile_id, content_hash)
-- ;;
CREATE TABLE style_entries (
	id VARCHAR(40) NOT NULL, 
	version_id VARCHAR(40) NOT NULL, 
	profile_id VARCHAR(40) NOT NULL, 
	category VARCHAR(14) NOT NULL, 
	group_key VARCHAR(80) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	body_json JSON NOT NULL, 
	summary TEXT NOT NULL, 
	conditions TEXT NOT NULL, 
	exceptions TEXT NOT NULL, 
	support_count INTEGER NOT NULL, 
	group_total INTEGER NOT NULL, 
	confidence VARCHAR(8) NOT NULL, 
	flags_json JSON NOT NULL, 
	ai_eligible BOOLEAN NOT NULL, 
	risk_ack_reason VARCHAR(300) NOT NULL, 
	origin VARCHAR(8) NOT NULL, 
	origin_entry_id VARCHAR(40), 
	review_status VARCHAR(10) NOT NULL, 
	review_note VARCHAR(300) NOT NULL, 
	reviewed_by VARCHAR(40), 
	reviewed_at DATETIME, 
	sort_order INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	row_version INTEGER NOT NULL, 
	CONSTRAINT pk_style_entries PRIMARY KEY (id), 
	CONSTRAINT ck_style_entries_category CHECK (category IN ('reasoning', 'principle', 'formula', 'herb_pair', 'modification', 'preference', 'outcome', 'followup', 'note')), 
	CONSTRAINT ck_style_entries_review_status CHECK (review_status IN ('pending', 'approved', 'rejected')), 
	CONSTRAINT fk_style_entries_version_id_style_versions FOREIGN KEY(version_id) REFERENCES style_versions (id) ON DELETE CASCADE, 
	CONSTRAINT fk_style_entries_profile_id_style_profiles FOREIGN KEY(profile_id) REFERENCES style_profiles (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_style_entries_version ON style_entries (version_id, category)
-- ;;
CREATE TABLE style_sources (
	id VARCHAR(40) NOT NULL, 
	import_id VARCHAR(40) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	original_name VARCHAR(200) NOT NULL, 
	storage_key VARCHAR(120) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	parse_status VARCHAR(12) NOT NULL, 
	parse_error VARCHAR(300) NOT NULL, 
	job_id VARCHAR(40), 
	parsed_json JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_style_sources PRIMARY KEY (id), 
	CONSTRAINT fk_style_sources_import_id_style_imports FOREIGN KEY(import_id) REFERENCES style_imports (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_style_sources_import ON style_sources (import_id)
-- ;;
CREATE TABLE style_entry_evidence (
	id INTEGER NOT NULL, 
	entry_id VARCHAR(40) NOT NULL, 
	case_id VARCHAR(40) NOT NULL, 
	kind VARCHAR(12) NOT NULL, 
	quote VARCHAR(300) NOT NULL, 
	detail_json JSON NOT NULL, 
	CONSTRAINT pk_style_entry_evidence PRIMARY KEY (id), 
	CONSTRAINT entry_case_kind UNIQUE (entry_id, case_id, kind), 
	CONSTRAINT fk_style_entry_evidence_entry_id_style_entries FOREIGN KEY(entry_id) REFERENCES style_entries (id) ON DELETE CASCADE, 
	CONSTRAINT fk_style_entry_evidence_case_id_style_cases FOREIGN KEY(case_id) REFERENCES style_cases (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_style_entry_evidence_case ON style_entry_evidence (case_id)
-- ;;
CREATE INDEX ix_style_entry_evidence_entry_id ON style_entry_evidence (entry_id)
