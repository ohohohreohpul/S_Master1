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
- [x] ElevenLabs V3 audio generation pipeline (65 segments: 4 instruction + 61 content audio)
- [x] Section instruction audio plays before each listening section
- [x] Audio preloading system (Blob URLs cached before exam starts)
- [x] Audio stored in MongoDB `audio_files` collection - generated ONCE, reused for all users (network effects)
- [x] Listening Module - all questions visible, sequential audio playback, section instructions
- [x] Reading Module - split panel (passage left, questions right), multiple question types
- [x] Writing Module - textarea with word count, task tabs, AI scoring via OpenRouter
- [x] Speaking Module - audio prompts + MediaRecorder recording + AI scoring
- [x] Improved answer scoring: case-insensitive, space-insensitive, accepts variations
- [x] Audio scripts include spelling for proper nouns (e.g., "P-E-D-A-L-G-O")
- [x] IELTS band score conversion for Listening/Reading
- [x] Results page with detailed answer review and band scores
- [x] Progress tracking dashboard
- [x] Admin panel with exam management, stats, generate/delete/regenerate
- [x] AI exam generation via OpenRouter + ElevenLabs
- [x] Framer-motion animations throughout (page transitions, staggered reveals, hover effects)
- [x] PlayStation-inspired design (light content, dark hero/nav, blue accents)
- [x] 22/22 backend tests passed

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
