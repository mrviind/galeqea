# TaskFlow: Software Requirements Specification (Sample Template)

**Document type:** Product Requirements Document
**Version:** 1.0
**Author:** Product Team
**Status:** Approved for build

> This is a bundled sample. Use it to see how GaleQEA turns a requirements document into a Test Plan, a test case register, an executed run, and a Test Completion Report, before you upload your own. Replace the sections below with your own product's requirements and upload the result the same way.

## 1. Introduction

TaskFlow is a lightweight team task tracker: users create tasks, assign them, track status, and get notified when something changes. This document describes what TaskFlow must do (functional requirements), how well it must do it (non-functional requirements), and the business rules that constrain both.

## 2. Scope

In scope: task creation and editing, assignment, comments, attachments, search and filtering, notifications, and role-based administration. Out of scope: billing, third-party calendar sync, and mobile native apps (v1 is web only).

## 3. Functional Requirements

**REQ-101: Sign in.** The system shall let a registered user sign in with an email and password.
- A correct email/password pair must sign the user in and land them on the dashboard.
- An incorrect password must be rejected with a generic "invalid credentials" message that does not reveal whether the email exists.
- After 5 consecutive failed attempts the account is locked for 15 minutes.

**REQ-102: Create a task.** The system shall allow an authenticated user to create a new task with a title, description, due date, priority, and assignee.
- The title is required and must be between 1 and 120 characters.
- Submitting with no title must be rejected with a validation error and the form must not clear.
- A newly created task defaults to status "Open" and appears at the top of the task list within 2 seconds.

**REQ-103: Edit a task.** The system shall allow the task's creator or its assignee to edit any of its fields except its creation date.
- Editing the due date to a date in the past must show a confirmation before saving.
- Every field edit is recorded in the task's activity log with the editor's name and a timestamp.

**REQ-104: Delete a task.** The system shall allow the task's creator or a workspace admin to permanently delete a task.
- Deletion must ask for confirmation, since it is irreversible.
- A deleted task must disappear from every list and search result immediately.

**REQ-105: Assign a task.** The system shall let a task be assigned to exactly one workspace member at a time.
- The assignee must receive a notification within 60 seconds of being assigned.
- Reassigning a task must notify both the previous and the new assignee.

**REQ-106: Comment on a task.** The system shall let any workspace member with access to a task add a comment to it.
- Comments must support @mentions that notify the mentioned member.
- An empty comment must be rejected without a network round trip.

**REQ-107: Attach a file.** The system shall let a user attach a file to a task, up to 25MB per file.
- Uploading a file over the limit must be rejected before the upload starts, with a clear size-limit message.
- Attached files must be scanned before they are made available for download.

**REQ-108: Search and filter.** The system shall let a user search tasks by title and filter the task list by status, assignee, and priority.
- A search with no matches must show an empty state, not an error.
- Filters must be combinable (e.g., "assignee = me AND status = open") and the result must update without a full page reload.

**REQ-109: Notifications.** The system shall notify a user, via an in-app badge and an email digest, whenever they are assigned a task, mentioned, or a task they own changes status. The notification experience must not require a page refresh to appear.
- A user must be able to turn off email notifications from their profile settings, independent of in-app notifications.

**REQ-110: Workspace roles.** The system shall support three workspace roles: Member, Manager, and Admin, with Admin able to invite, suspend, or remove other members.
- A Member must not be able to change another member's role.
- Suspending a member must immediately revoke their session on every device.

## 4. Non-Functional Requirements

**NFR-201: Performance.** The task list must load within 1.5 seconds for a workspace with up to 5,000 tasks, measured at the 95th percentile.

**NFR-202: Availability.** The service must be available 99.9% of the time per calendar month, excluding scheduled maintenance windows announced 48 hours in advance.

**NFR-203: Security.** All data in transit must be encrypted with TLS 1.2 or higher, and passwords must be stored using a salted, adaptive hash (bcrypt or stronger), never in plain text.

**NFR-204: Accessibility.** The web application must conform to WCAG 2.1 AA, including full keyboard navigation and screen-reader-friendly labeling for every interactive control.

**NFR-205: Browser support and responsiveness.** The application must be fully usable, with fast, responsive performance, on the current and previous major versions of Chrome, Firefox, Safari, and Edge.

## 5. Business Rules

**BR-301: Plan tiers.** A workspace's plan must be one of Free, Team, or Enterprise; task and member limits are enforced per the plan's published caps, and an over-limit action must be blocked with an upgrade prompt rather than silently allowed.

**BR-302: Data retention.** A deleted workspace's data must be retained for 30 days for recovery purposes, then permanently purged; this retention period is not configurable per customer.

## 6. Assumptions & Open Questions

- Single sign-on (SSO) is out of scope for v1; assumed to be a fast-follow, but the exact provider (Okta, Google Workspace, etc.) is still TBD.
- The notification email digest cadence (immediate vs. hourly batch) is undecided and needs product input.
- The device/performance budget behind NFR-205's "fast, responsive" language is a placeholder pending an actual benchmark from engineering.

## 7. Acceptance & Sign-off

This document is considered accepted once Product, Engineering, and QA leads have reviewed sections 3–6 and logged no open blocking comments.
