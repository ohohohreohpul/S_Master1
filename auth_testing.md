# Auth Testing Playbook for IELTS Mock Exam

## Test User Setup
Test user is created via MongoDB seed. Session token: test_session_1777464282346

## Cookie Injection for Browser Tests
```javascript
await page.context.add_cookies([{
    "name": "session_token",
    "value": "test_session_1777464282346",
    "domain": "ielts-mock-exam-2.preview.emergentagent.com",
    "path": "/",
    "httpOnly": true,
    "secure": true,
    "sameSite": "None"
}]);
```

## Backend API Tests
```bash
TOKEN="test_session_1777464282346"
API="https://ielts-mock-exam-2.preview.emergentagent.com/api"

# Auth
curl "$API/auth/me" -H "Authorization: Bearer $TOKEN"

# Exams
curl "$API/exams"
curl "$API/exams/exam_academic_001"

# Audio
curl "$API/audio/audio_82d3fdfeded8" -o test.mp3
```
