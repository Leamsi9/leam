"""Bounded assignment summary; reservations never prove external worker liveness."""
import json


def workload(backlog, snapshot, target=4):
    items = list(snapshot['items'])
    with backlog.store.connect() as db:
        has_updates = db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='deployment_updates'").fetchone()
        deployed = {}
        if has_updates:
            for row in db.execute('SELECT feature,body FROM deployment_updates WHERE superseded=0 ORDER BY sequence'):
                deployed[row['feature']] = json.loads(row['body']).get('qa', {}).get('state')
        # Published QA/review work is still the SAME canonical assignment, shown
        # on its Updates card rather than duplicated as a new backlog ticket.
        for row in db.execute("SELECT * FROM backlog_assessments"):
            item = backlog.item(row)
            if item['feature'] in deployed and item['deliveryState'] == 'in_progress':
                items.append({**item, 'deployed': True})
        predicate = "key LIKE 'backlog:deleted:%' AND json_extract(value,'$.cancellationRequired')=1 AND coalesce(json_extract(value,'$.cancellationAcknowledgedAt'),0)=0"
        cancellation_total = db.execute("SELECT count(*) FROM settings WHERE " + predicate).fetchone()[0]
        cancellations = []
        for row in db.execute("SELECT value FROM settings WHERE " + predicate + " ORDER BY key LIMIT 100"):
            value = json.loads(row["value"])
            cancellations.append({'feature': value['feature'], 'revision': value['revision']})
    workers = {}
    uncertain = []
    eligible = []
    for item in items:
        blocked = bool(item['blockers'] or any(s['state']=='blocked' or s.get('blocker') for s in item['subtasks']) or any(deployed.get(d) not in {'pending','passed'} for d in item['dependencies']))
        if item['deliveryState']=='in_progress':
            if blocked or item['stale'] or not item['worker'] or not item['owner']:
                uncertain.append(item['feature'])
            else:
                workers.setdefault(item['worker'], []).append(item['feature'])
        elif item['deliveryState'] in {'queued','ready'} and not blocked and not item.get('paused'):
            eligible.append({'feature':item['feature'],'rank':item['rank'],'revision':item['revision']})
    return {
        'target':target, 'recordedWorkerCount':len(workers),
        'recordedAssignments':workers, 'livenessVerified':False,
        'duplicateWorkers':{w:fs for w,fs in workers.items() if len(fs)>1},
        'uncertainAssignments':uncertain,
        'shortfall':max(0,target-len(workers)),
        'eligibleNext':eligible[:max(0,target-len(workers))],
        'eligibleCount':len(eligible), 'pendingCancellations':cancellations,
        'pendingCancellationCount':cancellation_total,
        'pendingCancellationsTruncated':cancellation_total > len(cancellations),
        'basis':'Advisory snapshot; compare external worker state and use revision-protected start before dispatch.',
    }
