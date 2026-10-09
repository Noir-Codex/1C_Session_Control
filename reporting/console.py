from __future__ import annotations

from onec.models import ServerSnapshot


def print_report(report: ServerSnapshot, connectivity_only: bool = False) -> None:
    print(f"\nRAS {report.server_name} ({report.endpoint}): OK")
    print(f"Clusters: {len(report.clusters)}")
    for snapshot in report.clusters:
        cluster = snapshot.cluster
        print(f"\nCluster: {cluster.name or '(unnamed)'} [{cluster.id}]")
        print(f"Infobases: {len(snapshot.infobases)}")
        for base in snapshot.infobases:
            print(f"  - {base.name or '(unnamed)'} [{base.id}]")
        if connectivity_only:
            continue
        print(f"Sessions: {len(snapshot.sessions)}")
        for session in snapshot.sessions:
            print(
                "  - "
                f"base={session.infobase_name or session.infobase_id or '-'}; "
                f"user={session.user_name or '-'}; host={session.host or '-'}; "
                f"app={session.app_id or '-'}; started={session.started_at or '-'}; "
                f"id={session.id or '-'}"
            )
