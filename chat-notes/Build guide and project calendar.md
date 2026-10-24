<!-- STATUS: REFERENCE - your messages + what was decided/built/left open | source: chat "Build guide and project calendar" (2026-10-23) -->
## User messages (verbatim)

### Message 1
My general idea for a build guide, as this project is aimed to be finished by end of month. Pick up on anything im missing, make this a calendar/todo/progress check artifact:

uild order:

planning - lunch friday
1. Prepare data diagram for AWS
2. Finalize the silver and gold schemas

afternoon friday: 
3. The run locally: using lca s3 files, parse into silver and gold. Port native parser from Appeals, refactor as needed. 

4. Learn more about current state and what is locked vs can be improved. See SOTA prior work architecture.
ASk Jaewon where project stands, what was on his todo before handoff, what stakeholders and sharon liked vs didnt like, how much he thinks should be imrpoved vs just left as MVP, with improvements later. 
5. focus on scoping out each AWS native component of the diagram further, possibly provisioning the terraform for each role/service

6. Try to build a full local clone of the architecture to validate logic before moving to AWS?


Weekend: 
1. Can go back and review best practices for this system design, what is typical used across corporations for multitenancy with PHI boundaries

2. Important to look out for: the design we have probably works fine, but does it scale for performance? Do we have system design principas built around rate limiting queries? Queues? etc

3. Based on the meeting with Jaewon, this is where all the improvement would go. 
    - Imporoving across 3 paralells: cost, accuracy, and saftey

    We want to lean on the OpenAI example, paired up with best practices from the paper. Importantly incorporate cost-reduction, something openai did not mention. 
    We could also greatly improve the frontend to clone the openai interface with artifacts. 
        Examples; Question prompts to the user for clairty. Regular text sent to the user. Plot sent to the user. markdown table. csv to downdload.


        We also need to create some docs and an internal knowlkege database. This should be scoped to the edi data as a whole for now. For now, no customer specific docs. Would need some admin role to set.

This is also when we would incorporate some of the approved impovements from Jaewon on the actual agent analyst architecture. The semantic layer, premade queries for most common questions.



 





Next week:
Monday:

0. Review design, prepare for all questions/pushback 


1. Design review the full backend. Hopefully, by then only small changes will need to be made to our data backend, and more scope for the auth will be revealed. Iterate

2. with all the terraform scoped our AWS hosting can happen fast, given we work with owen. Can start monday finish tuesday?

Tuesday:
continue whatever we coulnt monday, get something hosted and testable by EOD


By wednesday, we should have some product made and hosted somewhere, and can begin testing. Haiku swarm?? 

Thursday should be the last day, this is where we would go through formal deployment, possibly a demo, a full architecture breakdown, confluence documenting etc.

Friday: off

## Notes

### Decided
- Real deadline is Thu Oct 29 (Fri Oct 30 off, Oct 31 is a Saturday).
- Jaewon meeting moved to first thing Friday, since his answers decide what is locked vs open (schema freeze, terraform scope).
- Add a golden eval set (30-50 questions from verified_requests) on Saturday as the accuracy baseline for Sunday and Wednesday.
- Add an MVP cut line / scope freeze on Sunday before Monday's design review.
- Concrete cost levers: prompt caching, Haiku routing for simple asks, per-turn token budget, cost logged per turn. Safety levers: PHI-free logs, audit trail, cross-tenant probe that must fail, SQL guard on escape hatch.

### Built
- Published artifact "CC Build Week": day-by-day checklist Fri Oct 23 - Fri Oct 30 with sticky progress bar, blocker count, filters (All/Open/Blockers/Added), "added"/"moved"/"blocker" chips, today highlight, checks saved in localStorage only.
- Appended the stated delivery plan to the project memory file (implementations-neo4j-chatbot.md).

### Left open
- Governance blockers: Cognito approval (CoE 10/19), DuckDB/SQLite catalog exception, DEV PHI-bucket policy; whether LabCorp PHI files may be run locally at all.
- Neo4j still used for payer search in DuckDB mode: host Neo4j or move payer search to ref.payer_alias + sem.payers.
- ADR-010 (EC2) vs ADR-011 (Fargate) conflict must be settled before terraform.
- Owen's availability for Mon-Tue hosting unconfirmed.
- Known gaps to publish Thursday: 277CA/999, COB detail, payer_alias coverage, heuristic line matching.
- Checklist state is per-browser only (not shared/synced).
