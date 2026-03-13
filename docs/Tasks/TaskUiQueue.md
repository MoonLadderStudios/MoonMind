# Task UI Queue Layout Switching

Status: Active  
Owners: MoonMind Task UI  
Last Updated: 2026-03-13  

## 1. Purpose

Define the responsive layout contract for list surfaces rendered from `api_service/static/task_dashboard/dashboard.js`. The goal is to keep the existing dense table for desktop/tablet viewports while retaining a mobile-first card layout fed by the same normalized query rows (Temporal Visibility indexes and legacy Queue listings).

This covers the `/tasks/list` dashboards. Detailed workflows (`/tasks/:taskId`) retain their layout constraints.

## 2. Shared Row Definition

The components parse execution records into normalized properties: Source, Queue, Runtime, Status, Created, Started, Finished.
The Table maps these into standard UI grids, while the Mobile Card iterates these definitions to format `<dt>/<dd>` lists inside vertical blocks.

- The backend exposes these rows consistently over `GET /api/queue/jobs`.
- UI CSS governs the CSS media queries hiding the Table at `max-width: 767px` and showing Cards instead.
