-- Incidents carry a post code (sections.detail, as the ILG Forms incident form sends it)
-- and a separate address. Both go to the ILG Forms incident list.
ALTER TABLE sections ADD COLUMN IF NOT EXISTS address TEXT;
