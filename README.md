# [JstVerify](https://jstverify.com)
# Trust Your Application, JstVerify it! 
## CodePipeline Custom Action 

This application is intended to ease the setup requirements that allow direct CodePipeline integration with the JstVerify testing suite. 

### Requirements:

This application requires that you create a parameter store with the name ```JstVerifyAPIKey``` that will host your JstVerify API key. This allows direct communication to JstVerify API endpoints which in turn allows this application to run and check the status of tests. 

### Usage:

Once installed, this SAM template will create a custom action that will be available under the Testing category and will be titled ```Custom JstVerify (Version: *)```.

To utilize this testing provider, simply select the provider (titled above), specify a name for this action, then copy the test title (as viewed in the JstVerify dashboard) into the action input titled ```JstVerifyTestName```. 

### Resources Created:

This SAM template creates a CodePipeline custom action, supporting lambda function (and required IAM role/policy) and a CloudWatch rule that schedules the poller to run once a minute. This allows the lambda function to detect whenever CodePipeline runs with a JstVerify action and then takes the appropriate action (either run or check a test).