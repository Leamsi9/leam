import json
from datetime import UTC, datetime, timedelta

from leam_api.backlog import Assessment, Backlog
from leam_api.store import Store
from leam_api.updates import Publication, QA, Updates


def add(backlog, feature, **kwargs):
    return backlog.upsert(Assessment(feature=feature,title=feature,currentStep='Next bounded action',percent=10,**kwargs))


def test_status_counts_distinct_current_assignments_and_preserves_queue_rank(tmp_path):
    b=Backlog(Store(tmp_path))
    add(b,'first',deliveryState='in_progress',worker='one',owner='main')
    add(b,'duplicate',deliveryState='in_progress',worker='one',owner='main')
    add(b,'handover',deliveryState='handover',worker='two',owner='main')
    add(b,'stale',deliveryState='in_progress',worker='three',owner='main',assessedAt=datetime.now(UTC)-timedelta(hours=1))
    add(b,'missing',deliveryState='in_progress')
    add(b,'queued')
    snapshot=b.list()
    result=b.status()['workload']
    assert result['recordedWorkerCount']==1
    assert result['shortfall']==3
    assert set(result['duplicateWorkers']['one'])=={'first','duplicate'}
    assert set(result['uncertainAssignments'])=={'stale','missing'}
    assert result['eligibleNext']==[{'feature':'queued','rank':next(r['rank'] for r in snapshot['items'] if r['feature']=='queued'),'revision':1}]
    assert result['livenessVerified'] is False
    assert b.list()['ordering']==snapshot['ordering']


def test_status_excludes_missing_failed_dependencies_and_records_cancellation(tmp_path):
    store=Store(tmp_path);b=Backlog(store);u=Updates(store)
    failed=u.publish(Publication(feature='bad',title='Bad',summary='Failed deployed feature',deploymentId='release-1',deployedAt=datetime.now(UTC)))
    u.qa(failed['id'],QA(deploymentId='release-1',state='failed',details='Caller failed'))
    u.publish(Publication(feature='available',title='Available',summary='Deployed QA pending',deploymentId='release-1',deployedAt=datetime.now(UTC)))
    add(b,'blocked-failed',dependencies=['bad'])
    add(b,'blocked-missing',dependencies=['unknown'])
    add(b,'blocked-task',subtasks=[dict(id='test',title='Wait',state='blocked',blocker='External dependency')])
    add(b,'can-start',dependencies=['available'])
    with store.connect() as db:
        db.execute('INSERT INTO settings VALUES (?,?)',('backlog:deleted:old',json.dumps(dict(feature='old',revision=5,cancellationRequired=True,cancellationAcknowledgedAt=None,restoredAt=1))))
    result=b.status()['workload']
    assert [x['feature'] for x in result['eligibleNext']]==['can-start']
    assert result['pendingCancellations']==[{'feature':'old','revision':5}]
    assert result['eligibleCount']==1


def test_pending_cancellation_summary_is_bounded_and_explicit(tmp_path):
    store=Store(tmp_path);b=Backlog(store)
    with store.connect() as db:
        for i in range(105):
            db.execute('INSERT INTO settings VALUES (?,?)',('backlog:deleted:item-'+str(i),json.dumps(dict(feature='item-'+str(i),revision=1,cancellationRequired=True,cancellationAcknowledgedAt=None))))
    result=b.status()['workload']
    assert result['pendingCancellationCount']==105
    assert len(result['pendingCancellations'])==100
    assert result['pendingCancellationsTruncated'] is True
