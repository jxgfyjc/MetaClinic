-- 0001_baseline: initial schema of the 象元 AI 诊疗 v3 backend (generated, then frozen).
-- Statements are separated by `-- ;;` lines.
-- ;;
CREATE TABLE app_settings (
	"key" VARCHAR(64) NOT NULL, 
	value_json JSON, 
	updated_by VARCHAR(40) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_app_settings PRIMARY KEY ("key")
)
-- ;;
CREATE TABLE audit_events (
	id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	actor_kind VARCHAR(10) NOT NULL, 
	actor_id VARCHAR(40) NOT NULL, 
	actor_name VARCHAR(64) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	action VARCHAR(48) NOT NULL, 
	entity_type VARCHAR(24) NOT NULL, 
	entity_id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	summary VARCHAR(300) NOT NULL, 
	detail_json JSON NOT NULL, 
	ip VARCHAR(64) NOT NULL, 
	request_id VARCHAR(32) NOT NULL, 
	outcome VARCHAR(10) NOT NULL, 
	prev_hash VARCHAR(64) NOT NULL, 
	hash VARCHAR(64) NOT NULL, 
	legacy BOOLEAN NOT NULL, 
	CONSTRAINT pk_audit_events PRIMARY KEY (id)
)
-- ;;
CREATE INDEX ix_audit_events_actor ON audit_events (actor_id, ts)
-- ;;
CREATE INDEX ix_audit_events_entity ON audit_events (entity_type, entity_id)
-- ;;
CREATE INDEX ix_audit_events_patient ON audit_events (patient_id, ts)
-- ;;
CREATE INDEX ix_audit_events_ts ON audit_events (ts)
-- ;;
CREATE TABLE drugs (
	id VARCHAR(40) NOT NULL, 
	kind VARCHAR(8) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	generic_name VARCHAR(128) NOT NULL, 
	aliases_json JSON NOT NULL, 
	spec VARCHAR(64) NOT NULL, 
	form VARCHAR(24) NOT NULL, 
	unit VARCHAR(12) NOT NULL, 
	default_dose VARCHAR(48) NOT NULL, 
	default_frequency VARCHAR(32) NOT NULL, 
	default_route VARCHAR(24) NOT NULL, 
	drug_class VARCHAR(32) NOT NULL, 
	allergen_classes_json JSON NOT NULL, 
	pregnancy_note VARCHAR(120) NOT NULL, 
	contraindication TEXT NOT NULL, 
	note TEXT NOT NULL, 
	dose_min_g FLOAT, 
	dose_max_g FLOAT, 
	special_method VARCHAR(24) NOT NULL, 
	his_code VARCHAR(32) NOT NULL, 
	insurance_code VARCHAR(32) NOT NULL, 
	source VARCHAR(12) NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_drugs PRIMARY KEY (id), 
	CONSTRAINT kind_name_spec UNIQUE (kind, name, spec)
)
-- ;;
CREATE INDEX ix_drugs_drug_class ON drugs (drug_class)
-- ;;
CREATE INDEX ix_drugs_kind_name ON drugs (kind, name)
-- ;;
CREATE TABLE formulas (
	id VARCHAR(40) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	formula_type VARCHAR(12) NOT NULL, 
	source_note VARCHAR(160) NOT NULL, 
	indication TEXT NOT NULL, 
	herbs_json JSON NOT NULL, 
	decoction VARCHAR(300) NOT NULL, 
	contraindication TEXT NOT NULL, 
	dept VARCHAR(48) NOT NULL, 
	source VARCHAR(12) NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_formulas PRIMARY KEY (id)
)
-- ;;
CREATE INDEX ix_formulas_name ON formulas (name)
-- ;;
CREATE TABLE idempotency_keys (
	id INTEGER NOT NULL, 
	scope VARCHAR(120) NOT NULL, 
	"key" VARCHAR(120) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	status_code INTEGER NOT NULL, 
	response_json JSON, 
	created_at DATETIME NOT NULL, 
	completed_at DATETIME, 
	CONSTRAINT pk_idempotency_keys PRIMARY KEY (id), 
	CONSTRAINT scope_key UNIQUE (scope, "key")
)
-- ;;
CREATE TABLE jobs (
	id VARCHAR(40) NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	resource VARCHAR(16) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	priority INTEGER NOT NULL, 
	subject_type VARCHAR(24) NOT NULL, 
	subject_id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40), 
	idempotency_key VARCHAR(120), 
	input_json JSON NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	result_json JSON NOT NULL, 
	progress FLOAT NOT NULL, 
	progress_msg VARCHAR(200) NOT NULL, 
	error_code VARCHAR(40) NOT NULL, 
	error_detail TEXT NOT NULL, 
	attempts INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	run_after DATETIME NOT NULL, 
	locked_by VARCHAR(64) NOT NULL, 
	heartbeat_at DATETIME, 
	timeout_s INTEGER NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	created_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	cancel_requested BOOLEAN NOT NULL, 
	CONSTRAINT pk_jobs PRIMARY KEY (id), 
	CONSTRAINT ck_jobs_status CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')), 
	CONSTRAINT idempotency_key UNIQUE (idempotency_key)
)
-- ;;
CREATE INDEX ix_jobs_claim ON jobs (status, run_after, priority)
-- ;;
CREATE INDEX ix_jobs_encounter_id ON jobs (encounter_id)
-- ;;
CREATE INDEX ix_jobs_kind ON jobs (kind)
-- ;;
CREATE INDEX ix_jobs_subject ON jobs (subject_type, subject_id)
-- ;;
CREATE TABLE legacy_map (
	id INTEGER NOT NULL, 
	legacy_table VARCHAR(40) NOT NULL, 
	legacy_id VARCHAR(64) NOT NULL, 
	new_table VARCHAR(40) NOT NULL, 
	new_id VARCHAR(40) NOT NULL, 
	note VARCHAR(200) NOT NULL, 
	CONSTRAINT pk_legacy_map PRIMARY KEY (id), 
	CONSTRAINT legacy_row UNIQUE (legacy_table, legacy_id)
)
-- ;;
CREATE INDEX ix_legacy_map_new ON legacy_map (new_table, new_id)
-- ;;
CREATE TABLE patients (
	id VARCHAR(40) NOT NULL, 
	mrn VARCHAR(24) NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	gender VARCHAR(8) NOT NULL, 
	birth_date DATE, 
	age_years INTEGER, 
	phone VARCHAR(32) NOT NULL, 
	id_card VARCHAR(32) NOT NULL, 
	insurance VARCHAR(64) NOT NULL, 
	address VARCHAR(200) NOT NULL, 
	chronic_conditions_json JSON NOT NULL, 
	notes TEXT NOT NULL, 
	is_sample BOOLEAN NOT NULL, 
	dup_of_candidates_json JSON NOT NULL, 
	created_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	archived_at DATETIME, 
	row_version INTEGER NOT NULL, 
	CONSTRAINT pk_patients PRIMARY KEY (id)
)
-- ;;
CREATE UNIQUE INDEX ix_patients_mrn ON patients (mrn)
-- ;;
CREATE INDEX ix_patients_name ON patients (name)
-- ;;
CREATE INDEX ix_patients_phone ON patients (phone)
-- ;;
CREATE TABLE therapy_programs (
	id VARCHAR(40) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	category VARCHAR(16) NOT NULL, 
	applicable TEXT NOT NULL, 
	goal TEXT NOT NULL, 
	frequency VARCHAR(128) NOT NULL, 
	contraindications TEXT NOT NULL, 
	hospital_code VARCHAR(32) NOT NULL, 
	source VARCHAR(12) NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_therapy_programs PRIMARY KEY (id)
)
-- ;;
CREATE INDEX ix_therapy_programs_category ON therapy_programs (category)
-- ;;
CREATE INDEX ix_therapy_programs_name ON therapy_programs (name)
-- ;;
CREATE TABLE users (
	id VARCHAR(40) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	display_name VARCHAR(64) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	active BOOLEAN NOT NULL, 
	dept VARCHAR(64) NOT NULL, 
	title VARCHAR(64) NOT NULL, 
	license_no VARCHAR(64) NOT NULL, 
	phone VARCHAR(32) NOT NULL, 
	must_change_password BOOLEAN NOT NULL, 
	failed_logins INTEGER NOT NULL, 
	locked_until DATETIME, 
	last_login_at DATETIME, 
	profile_json JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT ck_users_role CHECK (role IN ('ADMIN', 'DOCTOR', 'NURSE', 'PHARMACIST'))
)
-- ;;
CREATE INDEX ix_users_role ON users (role)
-- ;;
CREATE UNIQUE INDEX ix_users_username ON users (username)
-- ;;
CREATE TABLE auth_sessions (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	expires_at DATETIME NOT NULL, 
	last_seen_at DATETIME NOT NULL, 
	revoked_at DATETIME, 
	ip VARCHAR(64) NOT NULL, 
	user_agent VARCHAR(255) NOT NULL, 
	CONSTRAINT pk_auth_sessions PRIMARY KEY (id), 
	CONSTRAINT fk_auth_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_auth_sessions_user_active ON auth_sessions (user_id, revoked_at)
-- ;;
CREATE TABLE encounters (
	id VARCHAR(40) NOT NULL, 
	encounter_no VARCHAR(24) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	doctor_id VARCHAR(40), 
	status VARCHAR(20) NOT NULL, 
	source VARCHAR(20) NOT NULL, 
	visit_type VARCHAR(20) NOT NULL, 
	prev_encounter_id VARCHAR(40), 
	appointment_id VARCHAR(40), 
	intake_id VARCHAR(40), 
	chief_complaint TEXT NOT NULL, 
	hpi TEXT NOT NULL, 
	past_history TEXT NOT NULL, 
	personal_family_history TEXT NOT NULL, 
	physical_exam TEXT NOT NULL, 
	symptoms_json JSON NOT NULL, 
	current_meds_json JSON NOT NULL, 
	vitals_json JSON NOT NULL, 
	special_status_json JSON NOT NULL, 
	tcm_four_json JSON NOT NULL, 
	patient_reported_json JSON NOT NULL, 
	inputs_version INTEGER NOT NULL, 
	inputs_hash VARCHAR(64) NOT NULL, 
	row_version INTEGER NOT NULL, 
	released_at DATETIME, 
	patient_summary_json JSON NOT NULL, 
	created_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	started_at DATETIME, 
	completed_at DATETIME, 
	cancel_reason VARCHAR(200) NOT NULL, 
	CONSTRAINT pk_encounters PRIMARY KEY (id), 
	CONSTRAINT ck_encounters_status CHECK (status IN ('WAITING', 'IN_PROGRESS', 'PENDING_CONFIRM', 'COMPLETED', 'CANCELLED')), 
	CONSTRAINT ck_encounters_source CHECK (source IN ('patient_intake', 'staff', 'appointment')), 
	CONSTRAINT ck_encounters_visit_type CHECK (visit_type IN ('initial', 'revisit', 'followup_visit')), 
	CONSTRAINT fk_encounters_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_encounters_doctor_id_users FOREIGN KEY(doctor_id) REFERENCES users (id), 
	CONSTRAINT fk_encounters_prev_encounter_id_encounters FOREIGN KEY(prev_encounter_id) REFERENCES encounters (id)
)
-- ;;
CREATE INDEX ix_encounters_doctor_id ON encounters (doctor_id)
-- ;;
CREATE INDEX ix_encounters_doctor_status ON encounters (doctor_id, status)
-- ;;
CREATE UNIQUE INDEX ix_encounters_encounter_no ON encounters (encounter_no)
-- ;;
CREATE INDEX ix_encounters_patient_created ON encounters (patient_id, created_at)
-- ;;
CREATE INDEX ix_encounters_patient_id ON encounters (patient_id)
-- ;;
CREATE TABLE job_events (
	id INTEGER NOT NULL, 
	job_id VARCHAR(40) NOT NULL, 
	ts DATETIME NOT NULL, 
	level VARCHAR(8) NOT NULL, 
	message VARCHAR(400) NOT NULL, 
	data_json JSON NOT NULL, 
	CONSTRAINT pk_job_events PRIMARY KEY (id), 
	CONSTRAINT fk_job_events_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_job_events_job ON job_events (job_id, ts)
-- ;;
CREATE TABLE patient_allergies (
	id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	allergen VARCHAR(128) NOT NULL, 
	reaction VARCHAR(128) NOT NULL, 
	severity VARCHAR(16) NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	confirmed BOOLEAN NOT NULL, 
	confirmed_by VARCHAR(40), 
	active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_patient_allergies PRIMARY KEY (id), 
	CONSTRAINT patient_allergen UNIQUE (patient_id, allergen), 
	CONSTRAINT fk_patient_allergies_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_patient_allergies_patient_id ON patient_allergies (patient_id)
-- ;;
CREATE TABLE access_tokens (
	id VARCHAR(40) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	doctor_id VARCHAR(40), 
	patient_id VARCHAR(40), 
	encounter_id VARCHAR(40), 
	parent_id VARCHAR(40), 
	label VARCHAR(80) NOT NULL, 
	expires_at DATETIME NOT NULL, 
	max_uses INTEGER NOT NULL, 
	use_count INTEGER NOT NULL, 
	revoked_at DATETIME, 
	created_by VARCHAR(40), 
	created_at DATETIME NOT NULL, 
	last_used_at DATETIME, 
	meta_json JSON NOT NULL, 
	CONSTRAINT pk_access_tokens PRIMARY KEY (id), 
	CONSTRAINT ck_access_tokens_kind CHECK (kind IN ('intake_link', 'intake_session', 'portal')), 
	CONSTRAINT fk_access_tokens_doctor_id_users FOREIGN KEY(doctor_id) REFERENCES users (id), 
	CONSTRAINT fk_access_tokens_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_access_tokens_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id), 
	CONSTRAINT fk_access_tokens_parent_id_access_tokens FOREIGN KEY(parent_id) REFERENCES access_tokens (id)
)
-- ;;
CREATE INDEX ix_access_tokens_kind_doctor ON access_tokens (kind, doctor_id)
-- ;;
CREATE UNIQUE INDEX ix_access_tokens_token_hash ON access_tokens (token_hash)
-- ;;
CREATE TABLE analyses (
	id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	"trigger" VARCHAR(16) NOT NULL, 
	inputs_version INTEGER NOT NULL, 
	inputs_hash VARCHAR(64) NOT NULL, 
	facts_json JSON NOT NULL, 
	job_id VARCHAR(40), 
	is_current BOOLEAN NOT NULL, 
	requested_by VARCHAR(40) NOT NULL, 
	models_json JSON NOT NULL, 
	prompt_versions_json JSON NOT NULL, 
	summary_json JSON NOT NULL, 
	error_code VARCHAR(40) NOT NULL, 
	error_detail TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	CONSTRAINT pk_analyses PRIMARY KEY (id), 
	CONSTRAINT ck_analyses_status CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')), 
	CONSTRAINT fk_analyses_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id)
)
-- ;;
CREATE INDEX ix_analyses_encounter_current ON analyses (encounter_id, is_current)
-- ;;
CREATE INDEX ix_analyses_encounter_id ON analyses (encounter_id)
-- ;;
CREATE TABLE appointments (
	id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40), 
	guest_name VARCHAR(64) NOT NULL, 
	guest_phone VARCHAR(32) NOT NULL, 
	doctor_id VARCHAR(40) NOT NULL, 
	slot_start DATETIME NOT NULL, 
	slot_end DATETIME NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	reason VARCHAR(200) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	encounter_id VARCHAR(40), 
	source_encounter_id VARCHAR(40), 
	created_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	cancelled_reason VARCHAR(200) NOT NULL, 
	CONSTRAINT pk_appointments PRIMARY KEY (id), 
	CONSTRAINT ck_appointments_status CHECK (status IN ('booked', 'checked_in', 'completed', 'cancelled', 'no_show')), 
	CONSTRAINT fk_appointments_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_appointments_doctor_id_users FOREIGN KEY(doctor_id) REFERENCES users (id), 
	CONSTRAINT fk_appointments_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id)
)
-- ;;
CREATE INDEX ix_appointments_doctor_slot ON appointments (doctor_id, slot_start)
-- ;;
CREATE INDEX ix_appointments_patient_id ON appointments (patient_id)
-- ;;
CREATE INDEX ix_appointments_status ON appointments (status)
-- ;;
CREATE TABLE followup_plans (
	id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	doctor_id VARCHAR(40), 
	status VARCHAR(10) NOT NULL, 
	template VARCHAR(24) NOT NULL, 
	schedule_json JSON NOT NULL, 
	revisit_advice VARCHAR(200) NOT NULL, 
	notes TEXT NOT NULL, 
	created_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	activated_at DATETIME, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_followup_plans PRIMARY KEY (id), 
	CONSTRAINT ck_followup_plans_status CHECK (status IN ('proposed', 'active', 'completed', 'cancelled')), 
	CONSTRAINT fk_followup_plans_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id), 
	CONSTRAINT fk_followup_plans_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_followup_plans_doctor_id_users FOREIGN KEY(doctor_id) REFERENCES users (id)
)
-- ;;
CREATE INDEX ix_followup_plans_encounter_id ON followup_plans (encounter_id)
-- ;;
CREATE INDEX ix_followup_plans_patient_id ON followup_plans (patient_id)
-- ;;
CREATE TABLE medical_records (
	id VARCHAR(40) NOT NULL, 
	record_no VARCHAR(24) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	doctor_id VARCHAR(40), 
	status VARCHAR(10) NOT NULL, 
	working_version_id VARCHAR(40), 
	final_version_id VARCHAR(40), 
	finalized_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	print_count INTEGER NOT NULL, 
	CONSTRAINT pk_medical_records PRIMARY KEY (id), 
	CONSTRAINT ck_medical_records_status CHECK (status IN ('draft', 'finalized')), 
	CONSTRAINT uq_medical_records_encounter_id UNIQUE (encounter_id), 
	CONSTRAINT fk_medical_records_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id), 
	CONSTRAINT fk_medical_records_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_medical_records_doctor_id_users FOREIGN KEY(doctor_id) REFERENCES users (id)
)
-- ;;
CREATE INDEX ix_medical_records_patient_id ON medical_records (patient_id)
-- ;;
CREATE UNIQUE INDEX ix_medical_records_record_no ON medical_records (record_no)
-- ;;
CREATE TABLE treatment_plans (
	id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	version_no INTEGER NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	dx_set_hash VARCHAR(64) NOT NULL, 
	dx_snapshot_json JSON NOT NULL, 
	inputs_version INTEGER NOT NULL, 
	inputs_hash VARCHAR(64) NOT NULL, 
	sections_requested_json JSON NOT NULL, 
	sections_state_json JSON NOT NULL, 
	content_json JSON NOT NULL, 
	ai_original_json JSON NOT NULL, 
	ai_used BOOLEAN NOT NULL, 
	models_json JSON NOT NULL, 
	prompt_versions_json JSON NOT NULL, 
	job_id VARCHAR(40), 
	error_code VARCHAR(40) NOT NULL, 
	error_detail TEXT NOT NULL, 
	created_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	confirmed_by VARCHAR(40), 
	confirmed_at DATETIME, 
	confirmed_content_hash VARCHAR(64) NOT NULL, 
	superseded_by VARCHAR(40), 
	supersede_reason VARCHAR(200) NOT NULL, 
	row_version INTEGER NOT NULL, 
	CONSTRAINT pk_treatment_plans PRIMARY KEY (id), 
	CONSTRAINT ck_treatment_plans_status CHECK (status IN ('generating', 'draft', 'confirmed', 'superseded', 'failed')), 
	CONSTRAINT encounter_version UNIQUE (encounter_id, version_no), 
	CONSTRAINT fk_treatment_plans_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id)
)
-- ;;
CREATE INDEX ix_treatment_plans_encounter_status ON treatment_plans (encounter_id, status)
-- ;;
CREATE TABLE analysis_modules (
	id VARCHAR(40) NOT NULL, 
	analysis_id VARCHAR(40) NOT NULL, 
	module VARCHAR(16) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	sort_order INTEGER NOT NULL, 
	model VARCHAR(64) NOT NULL, 
	prompt_version VARCHAR(40) NOT NULL, 
	output_json JSON NOT NULL, 
	raw_excerpt TEXT NOT NULL, 
	error_code VARCHAR(40) NOT NULL, 
	error_detail TEXT NOT NULL, 
	duration_ms INTEGER NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	CONSTRAINT pk_analysis_modules PRIMARY KEY (id), 
	CONSTRAINT ck_analysis_modules_status CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped', 'unavailable')), 
	CONSTRAINT analysis_module UNIQUE (analysis_id, module), 
	CONSTRAINT fk_analysis_modules_analysis_id_analyses FOREIGN KEY(analysis_id) REFERENCES analyses (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_analysis_modules_analysis_id ON analysis_modules (analysis_id)
-- ;;
CREATE TABLE diagnoses (
	id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	module VARCHAR(8) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	original_name VARCHAR(200) NOT NULL, 
	icd_code VARCHAR(24) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	origin VARCHAR(8) NOT NULL, 
	analysis_id VARCHAR(40), 
	candidate_key VARCHAR(40), 
	confidence VARCHAR(8) NOT NULL, 
	rationale TEXT NOT NULL, 
	evidence_json JSON NOT NULL, 
	doctor_note TEXT NOT NULL, 
	is_primary BOOLEAN NOT NULL, 
	sort_order INTEGER NOT NULL, 
	reviewed_by VARCHAR(40), 
	reviewed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	row_version INTEGER NOT NULL, 
	CONSTRAINT pk_diagnoses PRIMARY KEY (id), 
	CONSTRAINT ck_diagnoses_status CHECK (status IN ('pending', 'accepted', 'modified', 'rejected', 'withdrawn', 'superseded')), 
	CONSTRAINT ck_diagnoses_module CHECK (module IN ('western', 'tcm')), 
	CONSTRAINT analysis_candidate UNIQUE (analysis_id, candidate_key), 
	CONSTRAINT fk_diagnoses_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id), 
	CONSTRAINT fk_diagnoses_analysis_id_analyses FOREIGN KEY(analysis_id) REFERENCES analyses (id)
)
-- ;;
CREATE INDEX ix_diagnoses_encounter_status ON diagnoses (encounter_id, status)
-- ;;
CREATE TABLE followup_tasks (
	id VARCHAR(40) NOT NULL, 
	plan_id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	doctor_id VARCHAR(40), 
	seq INTEGER NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	due_at DATETIME NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	questions_json JSON NOT NULL, 
	reminder_shown_at DATETIME, 
	answered_at DATETIME, 
	closed_by VARCHAR(40), 
	closed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_followup_tasks PRIMARY KEY (id), 
	CONSTRAINT ck_followup_tasks_status CHECK (status IN ('scheduled', 'answered', 'closed', 'cancelled')), 
	CONSTRAINT fk_followup_tasks_plan_id_followup_plans FOREIGN KEY(plan_id) REFERENCES followup_plans (id) ON DELETE CASCADE, 
	CONSTRAINT fk_followup_tasks_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id), 
	CONSTRAINT fk_followup_tasks_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_followup_tasks_doctor_id_users FOREIGN KEY(doctor_id) REFERENCES users (id)
)
-- ;;
CREATE INDEX ix_followup_tasks_doctor_id ON followup_tasks (doctor_id)
-- ;;
CREATE INDEX ix_followup_tasks_due ON followup_tasks (status, due_at)
-- ;;
CREATE INDEX ix_followup_tasks_encounter_id ON followup_tasks (encounter_id)
-- ;;
CREATE INDEX ix_followup_tasks_patient_id ON followup_tasks (patient_id)
-- ;;
CREATE INDEX ix_followup_tasks_plan_id ON followup_tasks (plan_id)
-- ;;
CREATE TABLE intake_sessions (
	id VARCHAR(40) NOT NULL, 
	link_token_id VARCHAR(40), 
	session_token_id VARCHAR(40), 
	doctor_id VARCHAR(40), 
	status VARCHAR(12) NOT NULL, 
	draft_json JSON NOT NULL, 
	patient_id VARCHAR(40), 
	encounter_id VARCHAR(40), 
	submit_key VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	submitted_at DATETIME, 
	expires_at DATETIME NOT NULL, 
	client_ip VARCHAR(64) NOT NULL, 
	user_agent VARCHAR(255) NOT NULL, 
	CONSTRAINT pk_intake_sessions PRIMARY KEY (id), 
	CONSTRAINT ck_intake_sessions_status CHECK (status IN ('draft', 'submitted', 'expired', 'cancelled')), 
	CONSTRAINT fk_intake_sessions_link_token_id_access_tokens FOREIGN KEY(link_token_id) REFERENCES access_tokens (id), 
	CONSTRAINT fk_intake_sessions_session_token_id_access_tokens FOREIGN KEY(session_token_id) REFERENCES access_tokens (id), 
	CONSTRAINT fk_intake_sessions_doctor_id_users FOREIGN KEY(doctor_id) REFERENCES users (id), 
	CONSTRAINT fk_intake_sessions_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_intake_sessions_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id)
)
-- ;;
CREATE INDEX ix_intake_sessions_status ON intake_sessions (status)
-- ;;
CREATE TABLE prescriptions (
	id VARCHAR(40) NOT NULL, 
	rx_no VARCHAR(24) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	doctor_id VARCHAR(40) NOT NULL, 
	plan_id VARCHAR(40), 
	rx_type VARCHAR(8) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	title VARCHAR(128) NOT NULL, 
	formula_type VARCHAR(12) NOT NULL, 
	formula_source VARCHAR(128) NOT NULL, 
	dx_links_json JSON NOT NULL, 
	doses_count INTEGER, 
	decoction VARCHAR(300) NOT NULL, 
	herbal_usage VARCHAR(300) NOT NULL, 
	notes TEXT NOT NULL, 
	safety_json JSON NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	ai_generated BOOLEAN NOT NULL, 
	row_version INTEGER NOT NULL, 
	signed_by VARCHAR(40), 
	signed_at DATETIME, 
	signature_hash VARCHAR(64) NOT NULL, 
	override_reasons_json JSON NOT NULL, 
	review_by VARCHAR(40), 
	review_at DATETIME, 
	review_note TEXT NOT NULL, 
	dispensed_by VARCHAR(40), 
	dispensed_at DATETIME, 
	voided_by VARCHAR(40), 
	voided_at DATETIME, 
	void_reason VARCHAR(300) NOT NULL, 
	his_mode VARCHAR(8) NOT NULL, 
	his_status VARCHAR(16) NOT NULL, 
	his_ref VARCHAR(64) NOT NULL, 
	his_submitted_at DATETIME, 
	print_count INTEGER NOT NULL, 
	created_by VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_prescriptions PRIMARY KEY (id), 
	CONSTRAINT ck_prescriptions_status CHECK (status IN ('draft', 'signed', 'pharmacy_review', 'approved', 'dispensed', 'returned', 'voided')), 
	CONSTRAINT ck_prescriptions_rx_type CHECK (rx_type IN ('western', 'patent', 'herbal')), 
	CONSTRAINT fk_prescriptions_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id), 
	CONSTRAINT fk_prescriptions_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_prescriptions_doctor_id_users FOREIGN KEY(doctor_id) REFERENCES users (id), 
	CONSTRAINT fk_prescriptions_plan_id_treatment_plans FOREIGN KEY(plan_id) REFERENCES treatment_plans (id)
)
-- ;;
CREATE INDEX ix_prescriptions_encounter ON prescriptions (encounter_id, status)
-- ;;
CREATE INDEX ix_prescriptions_patient_id ON prescriptions (patient_id)
-- ;;
CREATE INDEX ix_prescriptions_plan_id ON prescriptions (plan_id)
-- ;;
CREATE INDEX ix_prescriptions_queue ON prescriptions (status, signed_at)
-- ;;
CREATE UNIQUE INDEX ix_prescriptions_rx_no ON prescriptions (rx_no)
-- ;;
CREATE TABLE record_versions (
	id VARCHAR(40) NOT NULL, 
	record_id VARCHAR(40) NOT NULL, 
	version_no INTEGER NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	parent_version_id VARCHAR(40), 
	content_json JSON NOT NULL, 
	snapshot_json JSON NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	author_id VARCHAR(40) NOT NULL, 
	author_name VARCHAR(64) NOT NULL, 
	note VARCHAR(400) NOT NULL, 
	signature_hash VARCHAR(64) NOT NULL, 
	legacy_source VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_record_versions PRIMARY KEY (id), 
	CONSTRAINT record_version UNIQUE (record_id, version_no), 
	CONSTRAINT ck_record_versions_kind CHECK (kind IN ('draft','final','amendment')), 
	CONSTRAINT fk_record_versions_record_id_medical_records FOREIGN KEY(record_id) REFERENCES medical_records (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_record_versions_record_id ON record_versions (record_id)
-- ;;
CREATE TABLE therapy_items (
	id VARCHAR(40) NOT NULL, 
	plan_id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	sort_order INTEGER NOT NULL, 
	category VARCHAR(16) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	source_type VARCHAR(12) NOT NULL, 
	program_id VARCHAR(40), 
	acupoints_json JSON NOT NULL, 
	detail_json JSON NOT NULL, 
	indications TEXT NOT NULL, 
	contraindications TEXT NOT NULL, 
	needs_confirmation_json JSON NOT NULL, 
	rationale TEXT NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	original_json JSON NOT NULL, 
	doctor_note TEXT NOT NULL, 
	reviewed_by VARCHAR(40), 
	reviewed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_therapy_items PRIMARY KEY (id), 
	CONSTRAINT ck_therapy_items_status CHECK (status IN ('pending','accepted','modified','rejected')), 
	CONSTRAINT fk_therapy_items_plan_id_treatment_plans FOREIGN KEY(plan_id) REFERENCES treatment_plans (id) ON DELETE CASCADE, 
	CONSTRAINT fk_therapy_items_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id)
)
-- ;;
CREATE INDEX ix_therapy_items_encounter_id ON therapy_items (encounter_id)
-- ;;
CREATE INDEX ix_therapy_items_plan ON therapy_items (plan_id)
-- ;;
CREATE TABLE artifacts (
	id VARCHAR(40) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	intake_id VARCHAR(40), 
	encounter_id VARCHAR(40), 
	patient_id VARCHAR(40), 
	storage_key VARCHAR(200) NOT NULL, 
	thumb_key VARCHAR(200) NOT NULL, 
	original_name VARCHAR(200) NOT NULL, 
	mime VARCHAR(64) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	width INTEGER, 
	height INTEGER, 
	duration_s FLOAT, 
	uploaded_by_kind VARCHAR(8) NOT NULL, 
	uploaded_by_id VARCHAR(40) NOT NULL, 
	purpose VARCHAR(24) NOT NULL, 
	proc_status VARCHAR(14) NOT NULL, 
	proc_job_id VARCHAR(40), 
	result_text TEXT NOT NULL, 
	edited_text TEXT NOT NULL, 
	result_json JSON NOT NULL, 
	confirmed_by VARCHAR(40), 
	confirmed_at DATETIME, 
	error_code VARCHAR(40) NOT NULL, 
	error_detail TEXT NOT NULL, 
	attempt_count INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	deleted_at DATETIME, 
	CONSTRAINT pk_artifacts PRIMARY KEY (id), 
	CONSTRAINT ck_artifacts_kind CHECK (kind IN ('audio', 'lab_image', 'tongue_image', 'image')), 
	CONSTRAINT ck_artifacts_proc_status CHECK (proc_status IN ('pending', 'processing', 'succeeded', 'failed', 'not_required')), 
	CONSTRAINT fk_artifacts_intake_id_intake_sessions FOREIGN KEY(intake_id) REFERENCES intake_sessions (id), 
	CONSTRAINT fk_artifacts_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id), 
	CONSTRAINT fk_artifacts_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id)
)
-- ;;
CREATE INDEX ix_artifacts_encounter_kind ON artifacts (encounter_id, kind)
-- ;;
CREATE INDEX ix_artifacts_intake_kind ON artifacts (intake_id, kind)
-- ;;
CREATE INDEX ix_artifacts_sha256 ON artifacts (sha256)
-- ;;
CREATE TABLE followup_messages (
	id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40), 
	task_id VARCHAR(40), 
	response_id VARCHAR(40), 
	sender_kind VARCHAR(8) NOT NULL, 
	sender_id VARCHAR(40) NOT NULL, 
	sender_name VARCHAR(64) NOT NULL, 
	body TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	read_at DATETIME, 
	client_key VARCHAR(64) NOT NULL, 
	CONSTRAINT pk_followup_messages PRIMARY KEY (id), 
	CONSTRAINT fk_followup_messages_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_followup_messages_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id), 
	CONSTRAINT fk_followup_messages_task_id_followup_tasks FOREIGN KEY(task_id) REFERENCES followup_tasks (id)
)
-- ;;
CREATE INDEX ix_followup_messages_thread ON followup_messages (patient_id, created_at)
-- ;;
CREATE TABLE followup_responses (
	id VARCHAR(40) NOT NULL, 
	task_id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	answers_json JSON NOT NULL, 
	free_text TEXT NOT NULL, 
	risk_level VARCHAR(12) NOT NULL, 
	rule_hits_json JSON NOT NULL, 
	ai_summary_json JSON NOT NULL, 
	submit_key VARCHAR(64) NOT NULL, 
	submitted_at DATETIME NOT NULL, 
	legacy_source VARCHAR(64) NOT NULL, 
	CONSTRAINT pk_followup_responses PRIMARY KEY (id), 
	CONSTRAINT task_submit UNIQUE (task_id, submit_key), 
	CONSTRAINT fk_followup_responses_task_id_followup_tasks FOREIGN KEY(task_id) REFERENCES followup_tasks (id), 
	CONSTRAINT fk_followup_responses_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_followup_responses_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id)
)
-- ;;
CREATE INDEX ix_followup_responses_encounter_id ON followup_responses (encounter_id)
-- ;;
CREATE INDEX ix_followup_responses_patient_id ON followup_responses (patient_id)
-- ;;
CREATE INDEX ix_followup_responses_task_id ON followup_responses (task_id)
-- ;;
CREATE TABLE prescription_events (
	id INTEGER NOT NULL, 
	prescription_id VARCHAR(40) NOT NULL, 
	ts DATETIME NOT NULL, 
	actor_id VARCHAR(40) NOT NULL, 
	actor_name VARCHAR(64) NOT NULL, 
	from_status VARCHAR(16) NOT NULL, 
	to_status VARCHAR(16) NOT NULL, 
	action VARCHAR(24) NOT NULL, 
	note VARCHAR(400) NOT NULL, 
	CONSTRAINT pk_prescription_events PRIMARY KEY (id), 
	CONSTRAINT fk_prescription_events_prescription_id_prescriptions FOREIGN KEY(prescription_id) REFERENCES prescriptions (id) ON DELETE CASCADE
)
-- ;;
CREATE INDEX ix_prescription_events_rx ON prescription_events (prescription_id, ts)
-- ;;
CREATE TABLE prescription_items (
	id VARCHAR(40) NOT NULL, 
	prescription_id VARCHAR(40) NOT NULL, 
	line_no INTEGER NOT NULL, 
	drug_id VARCHAR(40), 
	name VARCHAR(128) NOT NULL, 
	spec VARCHAR(64) NOT NULL, 
	form VARCHAR(24) NOT NULL, 
	dose_text VARCHAR(48) NOT NULL, 
	frequency VARCHAR(32) NOT NULL, 
	route VARCHAR(24) NOT NULL, 
	days INTEGER, 
	quantity FLOAT, 
	quantity_unit VARCHAR(12) NOT NULL, 
	dose_g FLOAT, 
	special VARCHAR(24) NOT NULL, 
	role VARCHAR(4) NOT NULL, 
	instructions VARCHAR(200) NOT NULL, 
	reason VARCHAR(200) NOT NULL, 
	in_catalog BOOLEAN NOT NULL, 
	flags_json JSON NOT NULL, 
	CONSTRAINT pk_prescription_items PRIMARY KEY (id), 
	CONSTRAINT line UNIQUE (prescription_id, line_no), 
	CONSTRAINT fk_prescription_items_prescription_id_prescriptions FOREIGN KEY(prescription_id) REFERENCES prescriptions (id) ON DELETE CASCADE
)
-- ;;
CREATE TABLE followup_alerts (
	id VARCHAR(40) NOT NULL, 
	response_id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	doctor_id VARCHAR(40), 
	level VARCHAR(12) NOT NULL, 
	rules_json JSON NOT NULL, 
	status VARCHAR(14) NOT NULL, 
	handled_by VARCHAR(40), 
	handled_at DATETIME, 
	handle_note TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_followup_alerts PRIMARY KEY (id), 
	CONSTRAINT fk_followup_alerts_response_id_followup_responses FOREIGN KEY(response_id) REFERENCES followup_responses (id), 
	CONSTRAINT fk_followup_alerts_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_followup_alerts_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id)
)
-- ;;
CREATE INDEX ix_followup_alerts_doctor_id ON followup_alerts (doctor_id)
-- ;;
CREATE INDEX ix_followup_alerts_open ON followup_alerts (status, level, created_at)
-- ;;
CREATE INDEX ix_followup_alerts_patient_id ON followup_alerts (patient_id)
-- ;;
CREATE INDEX ix_followup_alerts_response_id ON followup_alerts (response_id)
-- ;;
CREATE TABLE lab_items (
	id VARCHAR(40) NOT NULL, 
	encounter_id VARCHAR(40) NOT NULL, 
	patient_id VARCHAR(40) NOT NULL, 
	artifact_id VARCHAR(40), 
	abbr VARCHAR(32) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	value_text VARCHAR(64) NOT NULL, 
	value_num FLOAT, 
	unit VARCHAR(32) NOT NULL, 
	ref_low FLOAT, 
	ref_high FLOAT, 
	ref_text VARCHAR(64) NOT NULL, 
	flag VARCHAR(8) NOT NULL, 
	source VARCHAR(8) NOT NULL, 
	verified BOOLEAN NOT NULL, 
	verified_by VARCHAR(40), 
	verified_at DATETIME, 
	collected_at VARCHAR(32) NOT NULL, 
	sort_order INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	deleted_at DATETIME, 
	CONSTRAINT pk_lab_items PRIMARY KEY (id), 
	CONSTRAINT fk_lab_items_encounter_id_encounters FOREIGN KEY(encounter_id) REFERENCES encounters (id), 
	CONSTRAINT fk_lab_items_patient_id_patients FOREIGN KEY(patient_id) REFERENCES patients (id), 
	CONSTRAINT fk_lab_items_artifact_id_artifacts FOREIGN KEY(artifact_id) REFERENCES artifacts (id)
)
-- ;;
CREATE INDEX ix_lab_items_encounter ON lab_items (encounter_id)
-- ;;
CREATE INDEX ix_lab_items_patient_id ON lab_items (patient_id)
