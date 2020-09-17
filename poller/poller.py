import os
import json
import uuid
import boto3
import urllib3, certifi
import traceback
from enum import Enum

# Environment Variables
JSTVERIFY_ACTION_PROVIDER_NAME      = os.environ['JSTVERIFY_ACTION_PROVIDER_NAME']
JSTVERIFY_ACTION_PROVIDER_CATEGORY  = os.environ['JSTVERIFY_ACTION_PROVIDER_CATEGORY']
JSTVERIFY_ACTION_PROVIDER_VERSION   = os.environ['JSTVERIFY_ACTION_PROVIDER_VERSION']

debug = False

# Load necessary AWS SDK clients
code_pipeline = boto3.client('codepipeline')

print(f'Loading function. '
      f'Provider name: {JSTVERIFY_ACTION_PROVIDER_NAME}, '
      f'category: {JSTVERIFY_ACTION_PROVIDER_CATEGORY}, '
      f'version: {JSTVERIFY_ACTION_PROVIDER_VERSION}')

http = urllib3.PoolManager(
    cert_reqs='CERT_REQUIRED',
    ca_certs=certifi.where()
)

class JobFlowStatus(Enum):
    Running = 1
    Succeeded = 2
    Failed = 3

def getTestStatus(reportID):
    SSMclient = boto3.client('ssm')

    ssm_response = SSMclient.get_parameter(
        Name='JstVerifyAPIKey',
        WithDecryption=True
    )

    JSTVERIFY_API_KEY = ssm_response['Parameter']['Value']

    if debug:
        print("API Key: {}".format(JSTVERIFY_API_KEY))

    api_call = http.request(
        'GET',
        'https://api.jstverify.com/v1',
        headers={
            'Authentication': JSTVERIFY_API_KEY
        },
        fields={
            'checkReport': reportID
        }
    )

    return json.loads(api_call.data.decode())

def runTest(testTitle):
    SSMclient = boto3.client('ssm')

    ssm_response = SSMclient.get_parameter(
        Name='JstVerifyAPIKey',
        WithDecryption=True
    )

    JSTVERIFY_API_KEY = ssm_response['Parameter']['Value']

    if debug:
        print("API Key: {}".format(JSTVERIFY_API_KEY))

    api_call = http.request(
        'GET',
        'https://api.jstverify.com/v1',
        headers={
            'Authentication': JSTVERIFY_API_KEY
        },
        fields={
            'runTest': testTitle
        }
    )

    return json.loads(api_call.data.decode())

def should_process_event(event: object) -> bool:
    """
    Whether or not lambda function should process the incoming event.
    :param event: Event object, passed as lambda argument.
    :return: True if the event should be processed; False otherwise.
    """
    source = event.get('source', '')

    # always poll CodePipeline if triggered by CloudWatch scheduled event
    if source == 'aws.events':
        return True

    # process CodePipeline events
    if source == 'aws.codepipeline':
        action_type = event.get('detail', {}).get('type', {})
        owner = action_type.get('owner', '')
        provider = action_type.get('provider', '')
        category = action_type.get('category', '')
        version = action_type.get('version', '')

        return all([
            owner == 'Custom',
            provider == JSTVERIFY_ACTION_PROVIDER_NAME,
            category == JSTVERIFY_ACTION_PROVIDER_CATEGORY,
            version == JSTVERIFY_ACTION_PROVIDER_VERSION
        ])


def lambda_handler(event, context):
    # Log the received event
    print("Received event: " + json.dumps(event, indent=2))

    # Handle only custom events
    if not should_process_event(event):
        return

    try:
        jobs = get_active_jobs()

        for job in jobs:
            job_id = job['id']
            continuation_token = get_job_attribute(job, 'continuationToken', '')
            print(f'Processing job: {job_id} with ContinuationToken: {continuation_token}')

            try:
                process_job(job, job_id, continuation_token)
            except Exception:
                print(f'error during processing job: {job_id}')
                traceback.print_exc()
                mark_job_failed(job_id, continuation_token)

    except Exception:
        traceback.print_exc()
        raise


def process_job(job, job_id, continuation_token):
    # inform CodePipeline about that
    ack_response = code_pipeline.acknowledge_job(jobId=job_id, nonce=job['nonce'])
    if not continuation_token:
        print('Starting new job')
        start_new_job(job, job_id)
    else:
        # Get current job flow status
        job_flow_status = get_job_flow_status(continuation_token)
        print('Current job status: ' + job_flow_status.name)

        if job_flow_status == JobFlowStatus.Running:
            mark_job_in_progress(job_id, continuation_token)
        elif job_flow_status == JobFlowStatus.Succeeded:
            mark_job_succeeded(job_id, continuation_token)
        elif job_flow_status == JobFlowStatus.Failed:
            mark_job_failed(job_id, continuation_token)


def get_active_jobs():
    # Call DescribeJobs
    response = code_pipeline.poll_for_jobs(
        actionTypeId={
            'owner': 'Custom',
            'category': JSTVERIFY_ACTION_PROVIDER_CATEGORY,
            'provider': JSTVERIFY_ACTION_PROVIDER_NAME,
            'version': JSTVERIFY_ACTION_PROVIDER_VERSION
        },
        maxBatchSize=10
    )
    jobs = response.get('jobs', [])
    return jobs


def start_new_job(job, job_id):
    # start job execution flow
    configuration = get_job_attribute(job, 'actionConfiguration', {}).get('configuration', {})
    testTitle = configuration.get('JstVerifyTestName')

    reportID = runTest(testTitle)
    reportID = reportID.get('message', reportID['response'])

    if reportID == 'Internal Server Error':
        reportID = 'FAILED'

    print('Test Title: {}'.format(testTitle))

    if debug:
        print('Passed Job Data: {}'.format(job))

    if reportID != 'FAILED':
        print('Report ID: {}'.format(reportID))
        print('Test Status: {}'.format(getTestStatus(reportID)))
        # report progress to have a proper link on the console
        # and "register" continuation token for subsequent jobs
        progress_response = code_pipeline.put_job_success_result(
            jobId=job_id,
            continuationToken=reportID,
            executionDetails={
                'summary': 'Starting Test...',
                'externalExecutionId': reportID,
                'percentComplete': 0
            }
        )

    else:
        print('Failed to run test...')
        mark_job_failed(job_id, '')


def mark_job_failed(job_id, continuation_token):
    print('Marking Job as Failed...')

    if continuation_token:
        response = getTestStatus(continuation_token)
        response = json.dumps(response)
    else:
        response = 'Failed to start test..'

    failure_details = {
        'type': 'JobFailed',
        'message': response
    }

    if continuation_token:
        failure_details['externalExecutionId'] = continuation_token

    progress_response = code_pipeline.put_job_failure_result(jobId=job_id, failureDetails=failure_details)


def mark_job_succeeded(job_id, continuation_token):
    print('completing the job')
    progress_response = code_pipeline.put_job_success_result(
        jobId=job_id,
        executionDetails={
            'summary': 'Test Finished and Passed...',
            'externalExecutionId': continuation_token,
            'percentComplete': 100
        }
    )


def mark_job_in_progress(job_id, continuation_token):
    print('completing the job, preserving continuationToken')
    progress_response = code_pipeline.put_job_success_result(
        jobId=job_id,
        continuationToken=continuation_token
    )


def get_job_attribute(job, attribute, default):
    return job.get('data', {}).get(attribute, default)


def get_job_flow_status(reportID) -> JobFlowStatus:
    response = getTestStatus(reportID)
    status = response.get('PercentComplete', 'FAIL')
    testResult = response.get('testResult', 'RUNNING')

    print('Current Test Percent Complete: {}'.format(status))
    print('Current Test Results: {}'.format(testResult))

    if status >= 0 and status <= 99:
        return JobFlowStatus.Running
    elif status == 100 and testResult == 'PASS':
        return JobFlowStatus.Succeeded
    elif status == 100 and testResult == 'FAIL':
        return JobFlowStatus.Failed
    else:
        return JobFlowStatus.Failed
