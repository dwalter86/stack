-- Incidents carry a post code (sections.detail, as the ILG Forms incident form sends it)
-- and a separate address. Both go to the ILG Forms incident list.
ALTER TABLE sections ADD COLUMN IF NOT EXISTS address TEXT;

-- The incident's post code and address are not typed on the incident: they come from its
-- items (or from the ILG Forms incident form). These record what the incident list row holds,
-- so the first item to supply them fills the row once and later items never overwrite it.
ALTER TABLE ilgforms_section_links ADD COLUMN IF NOT EXISTS post_code TEXT;
ALTER TABLE ilgforms_section_links ADD COLUMN IF NOT EXISTS address TEXT;
