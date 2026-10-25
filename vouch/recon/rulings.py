"""Tax-manager memory is derived from attributed structured answers."""


def past(conn,case_id=None):
    query="SELECT * FROM learned_corrections" + (" WHERE case_id=?" if case_id else "") + " ORDER BY at,id"
    return [dict(r) for r in conn.execute(query,(case_id,) if case_id else ())]
