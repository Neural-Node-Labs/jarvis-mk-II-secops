# AI Software Architecture, Planning & Design Directive

## Purpose
This directive defines how an AI should approach architecting, planning, and designing software — before any code is written — mirroring how Claude thinks through structure, tradeoffs, and scope.

## 1. Clarify the Problem Before the Solution
- Identify what the software actually needs to do, for whom, and why — not just the feature list.
- Surface unstated constraints: scale (10 users vs. 10 million), team size, timeline, existing systems it must integrate with, budget/infra limits.
- If requirements are ambiguous on something architecturally significant (e.g., expected load, data sensitivity, multi-tenancy), ask before committing to a design — getting this wrong is expensive to undo later.

## 2. Define Scope and Success Criteria
- State explicitly what's in scope for this design and what's deliberately out of scope (and why).
- Define what "done" and "working" look like — concrete, testable criteria, not vague goals.
- Separate "must-have" from "nice-to-have" so the core design isn't distorted by edge features.

## 3. Start Simple, Design for the Problem You Have
- Default to the simplest architecture that satisfies the actual requirements — not the most impressive or "future-proof" one.
- Avoid premature optimization and speculative generality (e.g., microservices for a single-team app with low traffic, or a plugin system with no planned plugins).
- It's easier to evolve a simple, well-factored design later than to unwind an over-engineered one now.

## 4. Separate Concerns Clearly
- Identify the major responsibilities of the system (e.g., data access, business logic, presentation, integration with external services) and keep them loosely coupled.
- Each component should have a single, clear responsibility — if a module's purpose can't be described in one sentence, it's probably doing too much.
- Define clear interfaces/contracts between components so pieces can be built, tested, and changed independently.

## 5. Data Modeling First
- For most applications, the data model is the foundation — get entities, relationships, and constraints right before building features on top.
- Think about what data needs to be queried, how often, and at what scale — this drives database choice and schema design more than feature lists do.
- Plan for data integrity (constraints, validation) at the data layer, not just in application code.

## 6. Choose Technology Deliberately
- Prefer well-established, well-documented tools and frameworks over novel or niche ones unless there's a specific, justified reason.
- Match technology choices to the team's actual skills and the project's actual requirements — not to what's currently trendy.
- For each significant choice (language, framework, database, hosting), be able to articulate *why* — what alternatives were considered and what tradeoff was made.

## 7. Design for the Non-Functional Requirements That Matter
- **Security**: identify trust boundaries, authentication/authorization needs, and sensitive data early — these are hard to bolt on later.
- **Reliability**: identify failure modes (network errors, third-party outages, bad input) and how the system should degrade.
- **Performance**: design around realistic expected load, not worst-case hypotheticals — but know where the obvious bottlenecks would be if usage grows.
- **Maintainability**: favor designs a new developer could understand by reading the code and a short doc, not just the original author.
- Don't optimize for non-functional requirements that don't actually apply to this project (e.g., extreme scalability for an internal tool with 20 users).

## 8. Plan Incrementally
- Break the build into milestones that each produce something runnable/testable — avoid "big bang" designs where nothing works until everything is finished.
- Identify the riskiest or most uncertain parts of the design and tackle/validate those early (spike, prototype, or proof-of-concept) before committing broader effort.
- Sequence work so that core functionality exists before edge cases and polish.

## 9. Document Key Decisions and Tradeoffs
- Capture significant architectural decisions briefly: what was decided, what alternatives existed, and why this one was chosen (a lightweight ADR — Architecture Decision Record — format works well).
- Document assumptions the design depends on (e.g., "assumes single-region deployment," "assumes data volume stays under X") so future changes can revisit them if those assumptions change.
- Keep diagrams and docs proportional to the project's complexity — a one-page overview is often more useful than an elaborate diagram nobody updates.

## 10. Review the Design Before Building
- Walk through the main user/data flows against the proposed design — does it actually handle them cleanly, or are there awkward workarounds already needed?
- Check for single points of failure, unclear ownership of data/logic, and circular dependencies between components.
- Get a second opinion (human review) on significant architectural choices before large amounts of code depend on them — design mistakes are far cheaper to fix on paper.

## 11. Treat the Design as a Living Artifact
- Expect the design to evolve as implementation reveals new information — note where and why it changes.
- Periodically revisit whether the original assumptions (scale, scope, constraints) still hold, especially for long-running projects.
