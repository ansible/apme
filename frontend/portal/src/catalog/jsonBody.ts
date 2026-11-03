/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { json, type RequestHandler } from "express";

/**
 * JSON body parser for individual APME catalog routes that read `req.body`.
 *
 * This module mounts on the **catalog** plugin HTTP stack (`httpRouter.use`).
 * Middleware registered with `router.use(...)` therefore runs for every catalog
 * request — including core routes such as `POST /entities/by-refs`.
 *
 * **Do not** call `router.use(jsonBody)` (or `router.use(json())`). A global
 * parser consumes the request stream before the core catalog handler can read
 * it, which surfaces as HTTP 500 `stream is not readable` on Relations and
 * other by-refs consumers. Attach `jsonBody` only as route-level middleware on
 * POST/PUT handlers under `/apme/...` that need a body.
 *
 * Same rule as `catalog-backend-module-rhaap` (`router.ts`).
 *
 * @see router.test.ts — "does not consume request bodies for non-APME catalog POSTs"
 */
export const jsonBody: RequestHandler = json();
