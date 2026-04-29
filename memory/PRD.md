# IELTS Mock Exam SaaS Platform - PRD

## Original Problem Statement
Build the most powerful, best Mock exam for IELTS takers - a computer-based IELTS simulation platform with all 4 modules (Listening, Reading, Writing, Speaking). ElevenLabs V3 with Creative settings for natural audio. Audio must be pre-loaded before exam starts for perfect timer sync. Questions visible immediately (like real IELTS CBT).

## Architecture
- **Frontend**: React + Tailwind + Shadcn UI (PlayStation-inspired light theme)
- **Backend**: FastAPI + MongoDB
- **Audio**: ElevenLabs V3 (eleven_v3 model, mp3_44100_128, creative audio tags)
- **AI**: OpenRouter API (Gemini Flash for content gen, GPT-4o for scoring)
- **Auth**: Emergent Google OAuth

## User Personas
1. **IELTS Test Taker** - Takes practice exams, reviews scores, tracks progress
2. **Future: Tutor** - Assigns exams, reviews student results
3. **Future: Institution Admin** - Manages users and licences

## Core Requirements (Static)
- Full 4-module IELTS simulation (Listening, Reading, Writing, Speaking)
- Pre-generated audio via ElevenLabs V3 with creative tags
- Audio preloaded before exam timer starts
- Questions visible immediately (IELTS CBT style)
- AI scoring for Writing/Speaking via OpenRouter
- Objective scoring for Listening/Reading with IELTS band conversion
- Progress tracking across modules

## What's Been Implemented (April 29, 2026)
- [x] Emergent Google OAuth authentication
- [x] Pre-seeded IELTS Academic Practice Test 1 (40L + 40R + 2W + 10S questions)
- [x] ElevenLabs V3 audio generation pipeline (61 segments, 4 listening sections + speaking prompts)
- [x] Audio preloading system (Blob URLs cached before exam starts)
- [x] Listening Module - all questions visible, sequential audio playback, section navigation
- [x] Reading Module - split panel (passage left, questions right), multiple question types
- [x] Writing Module - textarea with word count, task tabs
- [x] Speaking Module - audio prompts + MediaRecorder recording
- [x] IELTS band score conversion for Listening/Reading
- [x] AI scoring for Writing (via OpenRouter GPT-4o) with 4-criteria breakdown
- [x] AI scoring for Speaking with 4-criteria breakdown
- [x] Results page with detailed answer review
- [x] Progress tracking dashboard
- [x] AI exam generation endpoint (creates new exams via OpenRouter + ElevenLabs)
- [x] PlayStation-inspired design (light content panels, dark hero/nav)
- [x] 14/14 backend tests passed

## Prioritized Backlog

### P0 (Critical)
- [ ] Question type diversity: Add matching_headings interactive UI, diagram labeling
- [ ] Speaking module: Send recorded audio to ElevenLabs STT for transcription instead of manual text
- [ ] Full test mode: Sequential module flow (L → R → W → S) with combined results

### P1 (Important)
- [ ] Review & flag system during exam (mark questions for review)
- [ ] Highlighted text in Reading passages (CBT feature)
- [ ] Strikethrough on MC options (CBT feature)
- [ ] Scratch pad / note-taking panel
- [ ] General Training pathway support
- [ ] Question pool management (admin panel)

### P2 (Nice to Have)
- [ ] Subscription tiers (Free / Pro / Tutor / Institution)
- [ ] Stripe payment integration
- [ ] Tutor dashboard with student management
- [ ] Performance analytics with weak area identification
- [ ] Audio caching via object storage (S3/R2) instead of MongoDB base64
- [ ] Multiple exam sets (pre-built + dynamic assembly)

## Next Tasks
1. Full test mode (all 4 modules in sequence)
2. STT integration for Speaking module
3. More question type UI (matching, diagram labeling)
4. Admin panel for content management
5. Stripe subscription integration
