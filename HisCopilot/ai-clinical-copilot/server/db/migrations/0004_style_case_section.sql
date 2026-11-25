-- 0004_style_case_section: persist parsed document section (e.g. 痹证与骨关节病) on style_cases.
-- ;;
ALTER TABLE style_cases ADD COLUMN section VARCHAR(80) NOT NULL DEFAULT ''
