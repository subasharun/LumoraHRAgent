---
doc_id: POL-006
title: Information Security and Acceptable Use Policy
owner: Information Security
effective_date: 2026-01-01
version: 5.1
---

# Information Security and Acceptable Use Policy

> Lumora Systems, Inc. is a fictional company. This document is synthetic and was written for an AI engineering course project.

## 1. Purpose and Scope

This policy protects Lumora's customers, employees, and business by setting minimum security requirements for everyone who uses Lumora systems or data. It applies to all employees, interns, and contractors, on every device and in every location, including offices, homes, coworking spaces, and while traveling. Violations may result in loss of access and disciplinary action up to and including termination of employment or contract.

## 2. Security Responsibilities

Every worker is responsible for:

- protecting their credentials and never sharing passwords, MFA codes, or access tokens;
- handling data according to its classification (Section 3);
- using only approved devices and software (Sections 4 and 7);
- reporting security incidents promptly (Section 9);
- completing annual security awareness training within 30 days of assignment. New hires complete training within their first 7 days (see Onboarding Guide, POL-010).

## 3. Data Classification

All Lumora information falls into one of four classes:

| Class | Examples | Handling rules |
|---|---|---|
| **Public** | Marketing website, published blog posts | No restrictions |
| **Internal** | Org charts, internal wiki, most policies | Share only with Lumora workers |
| **Confidential** | Product roadmaps, source code, financial forecasts, employee records | Share on a need-to-know basis; encrypt in transit; never post in public channels |
| **Restricted** | Customer production data, payment data, health information, government IDs, security keys | Access only through approved systems; never download to local storage; never access from public locations or from outside the home country without written Information Security approval |

When in doubt, treat information as Confidential.

Employee personal data (compensation, performance, health, leave reasons, home addresses) is **Confidential** at minimum. Health and government ID information is **Restricted**. HR systems, including AI assistants that access HR data, must only display an employee's own records unless the user is authorized through PeopleHub roles.

## 4. Devices

- Company work must be performed on Lumora-issued devices that are enrolled in device management. Personal computers may not be used for Company work.
- Lumora-managed mobile phones or personal phones enrolled in the mobile management profile may be used for email, chat, and calendar only.
- Full-disk encryption, automatic screen lock after 5 minutes, and automatic security updates must remain enabled. Users may not disable endpoint protection.
- Devices must not be shared with family members or others.

Device issue, replacement, and return are covered in the Equipment and IT Policy (POL-011).

## 5. Network Access and Remote Work

- The Company VPN must be used on any network other than a Lumora office network, including home networks, coworking spaces, hotels, and airports.
- Public Wi-Fi may be used only with the VPN connected. Restricted data may never be accessed from a public location, even with the VPN.
- Home routers should use WPA2 or WPA3 encryption and a non-default administrator password.
- When working abroad, the additional requirements in POL-005, Section 7 apply, and some systems are blocked from outside the home country.

## 6. Authentication

Multi-factor authentication (MFA) is required for all Lumora systems. Hardware security keys are required for engineers with production access and for People Operations and Finance staff. Passwords must be at least 14 characters and must be stored in the Company password manager. Never approve an MFA prompt you did not initiate; report unexpected prompts as a security incident.

## 7. Software and AI Tools

- Only software from the Lumora Software Catalog may be installed. Requests for new software go through the IT Service Desk and require a security review.
- **Generative AI tools:** Only AI tools approved by Information Security may be used with Internal, Confidential, or Restricted data. Public consumer AI tools may be used only with Public data. Never paste source code, customer data, employee data, or credentials into an unapproved AI tool.
- Browser extensions must be from the approved list.

## 8. Email, Chat, and Phishing

Be suspicious of unexpected messages that ask you to log in, pay an invoice, buy gift cards, change payroll details, or share data. Report suspected phishing using the "Report Phishing" button. People Operations will **never** ask you to confirm your bank details or password by email or chat; payroll changes are made only inside PeopleHub.

## 9. Incident Reporting

Report any of the following to the IT Service Desk or the #security-incidents channel **within 1 hour of discovery**:

- a lost or stolen device or security key;
- a suspected phishing compromise, or credentials entered on a suspicious site;
- Confidential or Restricted data sent to the wrong person or posted publicly;
- malware warnings or unusual device behavior.

Reporting quickly matters more than knowing whether something is really an incident. Employees who report in good faith will not be disciplined for the report itself.

## 10. Clean Desk and Physical Security

Lock your screen whenever you step away. Do not leave printed Confidential or Restricted documents unattended, and use the secure shred bins in offices. Office badges must be visible and may not be shared. Visitors must be registered and escorted.

## 11. Travel

- Travel to destinations on the high-risk list requires a travel loaner device from IT; personal and primary devices must stay home.
- Travel to restricted countries with any Lumora device is prohibited (POL-005, Section 6).
- Use a privacy screen when working in public places such as airplanes and trains.

## 12. Monitoring

Lumora monitors its systems and networks for security purposes in accordance with applicable law. Workers should not expect privacy in their use of Lumora systems, except as required by law. Monitoring data is not used for productivity scoring.

## 13. Exceptions

Exceptions to this policy require written approval from Information Security, must be time-limited, and are recorded in the security exception register.
