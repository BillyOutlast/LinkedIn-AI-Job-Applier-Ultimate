"""CSS/XPath constants for Workday widgets.

All selectors target Workday's `data-automation-id` test hooks, which are
stable across tenants since 2018. The two non-`data-automation-id` constants
are URL patterns, exempt from the test in test_workday_selectors.py.
"""

# --- Account creation -------------------------------------------------------
ACCOUNT_CREATE_EMAIL_INPUT = "input[data-automation-id='email']"
ACCOUNT_CREATE_PASSWORD_INPUT = "input[data-automation-id='password']"
ACCOUNT_CREATE_VERIFY_PASSWORD_INPUT = "input[data-automation-id='verifyPassword']"
ACCOUNT_CREATE_SUBMIT = "button[data-automation-id='createAccountSubmitButton']"

# --- Apply flow container ---------------------------------------------------
APPLY_FLOW_CONTAINER = "[data-automation-id='applyFlowContainer']"
APPLICATION_CONFIRMATION = "[data-automation-id='applicationConfirmation']"

# --- My Experience phase ----------------------------------------------------
RESUME_UPLOAD_INPUT = "input[type='file'][data-automation-id='file-upload-input']"
WORK_HISTORY_ADD_BUTTON = "button[data-automation-id='add-work-experience']"
WORK_HISTORY_EMPLOYER = "input[data-automation-id='employer']"
WORK_HISTORY_TITLE = "input[data-automation-id='jobTitle']"
WORK_HISTORY_START_DATE = "input[data-automation-id='startDate']"
WORK_HISTORY_END_DATE = "input[data-automation-id='endDate']"
WORK_HISTORY_DESCRIPTION = "textarea[data-automation-id='description']"
EDUCATION_ADD_BUTTON = "button[data-automation-id='add-education']"
EDUCATION_SCHOOL = "input[data-automation-id='school']"
EDUCATION_DEGREE = "input[data-automation-id='degree']"
EDUCATION_START_DATE = "input[data-automation-id='educationStartDate']"
EDUCATION_END_DATE = "input[data-automation-id='educationEndDate']"
SKILLS_INPUT = "input[data-automation-id='skillsInput']"
SAVE_AND_CONTINUE = "button[data-automation-id='bottom-navigation-next-button']"

# --- Voluntary Disclosures --------------------------------------------------
DISCLOSURE_RADIO_GROUP = "[data-automation-id='radioGroup']"

# --- Custom Questions -------------------------------------------------------
QUESTION_TEXT_INPUT = "input[data-automation-id='textInput']"
QUESTION_TEXTAREA = "textarea[data-automation-id='textAreaField']"
QUESTION_DROPDOWN = "select[data-automation-id='select']"
QUESTION_CHECKBOX = "input[type='checkbox'][data-automation-id='checkbox']"
QUESTION_RADIO = "input[type='radio'][data-automation-id='radio']"

# --- Review + Submit --------------------------------------------------------
REVIEW_PAGE_INDICATOR = "[data-automation-id='reviewPage']"
SUBMIT_BUTTON = "button[data-automation-id='bottom-navigation-submit-button']"

# --- URL patterns (exempt from data-automation-id check) ---------------------
WORKDAY_HOST_SUFFIX = "myworkdayjobs.com"
APPLY_FLOW_URL_PATTERN = "/apply"
