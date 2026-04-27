// calendar.js
// Evaluates whether a GTFS service_id is active on a given date,
// using the calendarMap (from calendar.txt) and exceptionMap (from calendar_dates.txt).

function formatDate(date) {
  // Returns "YYYYMMDD" string matching GTFS date format
  const y  = date.getFullYear();
  const mo = String(date.getMonth() + 1).padStart(2, '0');
  const da = String(date.getDate()).padStart(2, '0');
  return `${y}${mo}${da}`;
}

export function isServiceActive(serviceId, date, calendarMap, exceptionMap) {
  const dateStr = formatDate(date);
  const key = `${serviceId}:${dateStr}`;

  // calendar_dates.txt exceptions override the regular calendar
  if (exceptionMap.has(key)) {
    return exceptionMap.get(key) === 1; // 1 = service added, 2 = service removed
  }

  const cal = calendarMap.get(serviceId);
  if (!cal) return false;
  if (dateStr < cal.start || dateStr > cal.end) return false;

  // JS Date.getDay(): 0=Sunday, 1=Monday … 6=Saturday
  // GTFS calendar.txt cols: days[0]=monday … days[5]=saturday, days[6]=sunday
  const gtfsColIndex = (date.getDay() + 6) % 7;
  return cal.days[gtfsColIndex] === '1';
}

export function getActiveServices(date, calendarMap, exceptionMap) {
  const result = new Set();
  for (const sid of calendarMap.keys()) {
    if (isServiceActive(sid, date, calendarMap, exceptionMap)) {
      result.add(sid);
    }
  }
  // Also add any services that have an exception_type=1 (added) on this date
  // even if they're not in calendar.txt (edge case, good practice)
  const dateStr = formatDate(date);
  for (const [key, type] of exceptionMap) {
    if (type === 1 && key.endsWith(':' + dateStr)) {
      const sid = key.slice(0, -(dateStr.length + 1));
      result.add(sid);
    }
  }
  return result;
}
