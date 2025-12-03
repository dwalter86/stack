const DEFAULT_INTERVAL_MS = 60000;
const MIN_INTERVAL_MS = 10000;

const requestedInterval = Number(window.AUTO_PAGE_UPDATE_INTERVAL_MS);
const intervalMs = Number.isFinite(requestedInterval) && requestedInterval >= MIN_INTERVAL_MS
  ? requestedInterval
  : DEFAULT_INTERVAL_MS;

function shouldSkipUpdate(){
  const active = document.activeElement;
  const activeTag = active?.tagName;
  const blockingTags = ['INPUT','TEXTAREA','SELECT'];
  if(blockingTags.includes(activeTag)) return true;
  if(document.querySelector('[data-disable-auto-update="true"]')) return true;
  return false;
}

function refreshPage(){
  if(document.visibilityState !== 'visible') return;
  if(shouldSkipUpdate()) return;
  window.location.reload();
}

const timerId = setInterval(refreshPage, intervalMs);

document.addEventListener('visibilitychange', () => {
  if(document.visibilityState === 'visible'){
    refreshPage();
  }
});

window.addEventListener('beforeunload', () => {
  clearInterval(timerId);
}, { once:true });