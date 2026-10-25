"""Scripted people are opt-in scenario facts, not automatic human approvals."""
from vouch.agent.questions import answer


def respond(conn,responses):
    answered=[]
    for request in conn.execute("SELECT * FROM info_requests WHERE status='pending'").fetchall():
        for response in responses:
            if response.get("person")==request["recipient"] and response.get("about")==request["about"]:
                answer(conn,request["request_id"],request["recipient"],response["data"])
                answered.append(request["request_id"])
                break
    conn.commit()
    return {"mode":"scripted","answered":answered}
