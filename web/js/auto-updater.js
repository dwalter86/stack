const DEFAULT_INTERVAL_MS = 60000;
const MIN_INTERVAL_MS = 10000;

function isAutoUpdaterEnabled(){
  const rawValue = window.AUTO_PAGE_UPDATER_ENABLED;
  if(rawValue === undefined || rawValue === null) return false;
  if(typeof rawValue === 'boolean') return rawValue;
  if(typeof rawValue === 'number') return rawValue === 1;
  if(typeof rawValue === 'string'){
    const normalized = rawValue.trim().toLowerCase();
    return normalized === '1' || normalized === 'true' || normalized === 'yes' || normalized === 'on';
  }
  return false;
}

function getIntervalMs(){
  const requestedInterval = Number(window.AUTO_PAGE_UPDATE_INTERVAL_MS);
  const isValidInterval = Number.isFinite(requestedInterval) && requestedInterval >= MIN_INTERVAL_MS;
  return isValidInterval ? requestedInterval : DEFAULT_INTERVAL_MS;
}

function shouldSkipUpdate(){
  const active = document.activeElement;
  const activeTag = active?.tagName;
  const blockingTags = ['INPUT', 'TEXTAREA', 'SELECT'];
  if(blockingTags.includes(activeTag)) return true;
  if(document.querySelector('[data-disable-auto-update="true"]')) return true;
  return false;
}

function refreshPage(){
  if(document.visibilityState !== 'visible') return;
  if(shouldSkipUpdate()) return;
  window.location.reload();
}

if(isAutoUpdaterEnabled()){
  const timerId = setInterval(refreshPage, getIntervalMs());

  document.addEventListener('visibilitychange', () => {
    if(document.visibilityState === 'visible'){
      refreshPage();
    }
  });

  window.addEventListener('beforeunload', () => {
    clearInterval(timerId);
  }, { once: true });
}