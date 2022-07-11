import sys
import argparse
import os
import yaml
import json
import urllib3 as urllib
import logging

PROBLEM_LEVELS = {
    "warning": 1,
    "error": 2,
}


class Colors:
    """Class containing color codes for printing strings to output."""

    black = "30m"
    red = "31m"
    green = "32m"
    yellow = "33m"
    blue = "34m"
    magenta = "35m"
    cyan = "36m"
    white = "37m"


class LintFinding(object):
    """Represents a linting problem."""

    def __init__(self, description="<no description>", level=None):
        self.description = description
        self.level = level


def get_max_error_level(findings):
    """Get max error level from list of findings."""
    if len(findings) == 0:
        return 0
    max_problem = max(findings, key=lambda finding: PROBLEM_LEVELS[finding.level])
    max_problem_level = PROBLEM_LEVELS[max_problem.level]
    return max_problem_level


def print_finding(finding: LintFinding):
    """Print formatted and colored finding."""
    if finding.level == "warning":
        color = Colors.yellow
    elif finding.level == "error":
        color = Colors.red
    else:
        color = Colors.white

    line = f"  - \033[{color}{finding.level}\033[0m {finding.description}"

    print(line)


def get_github_api_response(url, action_id):
    """Call GitHub API with error logging without throwing an exception."""
    http = urllib.PoolManager()
    headers = {"user-agent": "bw-linter"}

    if os.getenv("GITHUB_TOKEN", None):
        headers["Authorization"] = f"Token {os.environ['GITHUB_TOKEN']}"

    response = http.request("GET", url, headers=headers)

    if response.status == 403 and response.reason == "rate limit exceeded":
        logging.error(
            f"Failed to call GitHub API for action: {action_id} due to rate limit exceeded."
        )
        return None

    if response.status == 401 and response.reason == "Unauthorized":
        logging.error(
            f"Failed to call GitHub API for action: {action_id}: {response.data}."
        )
        return None

    return response


def action_repo_exists(action_id):
    """
    Takes and action id and checks if the action repo exists.

    Example action_id: bitwarden/gh-actions/version-bump@03ad9a873c39cdc95dd8d77dbbda67f84db43945
    """

    if "./" in action_id:
        # Handle local workflow calls, return None since there will be no updates.
        return True

    path, *hash = action_id.split("@")

    if "bitwarden" in path:
        path_list = path.split("/", 2)
        url = f"https://api.github.com/repos/{path_list[0]}/{path_list[1]}"
        r = get_github_api_response(url, action_id)

    else:
        r = get_github_api_response(f"https://api.github.com/repos/{path}", action_id)

    if r is None:
        # Handle github api limit exceed by returning that the action exists without actually checking
        # to prevent false errors on linter output. Only show it as an linter error.
        return True

    if r.status == 404:
        return False

    return True


def workflow_files(input: str) -> list:
    """
    Takes in an argument of directory and/or files in string format from the CLI.
    Returns a sorted set of all workflow files in the path(s) specified.
    """
    workflow_files = []
    for path in input.split():
        if os.path.isfile(path):
            workflow_files.append(path)
        elif os.path.isdir(path):
            for subdir, dirs, files in os.walk(path):
                for filename in files:
                    filepath = subdir + os.sep + filename
                    if filepath.endswith((".yml", ".yaml")):
                        workflow_files.append(filepath)

    return sorted(set(workflow_files))


def get_action_update(action_id):
    """
    Takes in an action id (bitwarden/gh-actions/version-bump@03ad9a873c39cdc95dd8d77dbbda67f84db43945)
    and checks the action repo for the newest version.
    If there is a new version, return the url to the updated version.
    """
    if "./" in action_id:
        # Handle local workflow calls, return None since there will be no updates.
        return None

    path, *hash = action_id.split("@")

    if "bitwarden" in path:
        path_list = path.split("/", 2)
        url = f"https://api.github.com/repos/{path_list[0]}/{path_list[1]}/commits?path={path_list[2]}"
        r = get_github_api_response(url, action_id)
        if r is None:
            return None

        sha = json.loads(r.data)[0]["sha"]
        if sha not in hash:
            return f"https://github.com/{path_list[0]}/{path_list[1]}/commit/{sha}"
    else:
        # Get tag from latest release
        r = get_github_api_response(
            f"https://api.github.com/repos/{path}/releases/latest", action_id
        )
        if r is None:
            return None

        tag_name = json.loads(r.data)["tag_name"]

        # Get the URL to the commit for the tag
        r = get_github_api_response(
            f"https://api.github.com/repos/{path}/git/ref/tags/{tag_name}", action_id
        )
        if r is None:
            return None

        if json.loads(r.data)["object"]["type"] == "commit":
            sha = json.loads(r.data)["object"]["sha"]
        else:
            url = json.loads(r.data)["object"]["url"]
            # Follow the URL and get the commit sha for tags
            r = get_github_api_response(url, action_id)
            if r is None:
                return None

            sha = json.loads(r.data)["object"]["sha"]

        if sha not in hash:
            return f"https://github.com/{path}/commit/{sha}"


def lint(filename):

    findings = []
    max_error_level = 0

    with open(filename) as file:
        workflow = yaml.load(file, Loader=yaml.FullLoader)

        # Check for 'name' key for the workflow.
        if "name" not in workflow:
            findings.append(LintFinding("Name key missing for workflow.", "warning"))

        # Check for 'name' value to be capitalized in workflow.
        elif not workflow["name"][0].isupper():
            findings.append(
                LintFinding(
                    f"Name value for workflow is not capitalized. [{workflow['name']}]",
                    "warning",
                )
            )

        # Loop through jobs in workflow.
        if "jobs" in workflow:
            jobs = workflow["jobs"]
            for job_key in jobs:
                job = jobs[job_key]

                # Make sure runner is using pinned version.
                runner = job.get("runs-on", "")
                if "-latest" in runner:
                    findings.append(
                        LintFinding(
                            f"Runner version is set to '{runner}', but needs to be pinned to a version.",
                            "warning",
                        )
                    )

                # Check for 'name' key for job.
                if "name" not in job:
                    findings.append(
                        LintFinding(
                            f"Name key missing for job key '{job_key}'.", "warning"
                        )
                    )
                # Check for 'name' value to be capitalized in job.
                elif not job["name"][0].isupper():
                    findings.append(
                        LintFinding(
                            f"Name value of job key '{job_key}' is not capitalized. [{job['name']}]",
                            "warning",
                        )
                    )

                # If the job has environment variables defined, then make sure they start with an underscore.
                if "env" in job:
                    for k in job["env"].keys():
                        if k[0] != "_":
                            findings.append(
                                LintFinding(
                                    f"Environment variable '{k}' of job key '{job_key}' does not start with an underscore.",
                                    "warning",
                                )
                            )

                # Loop through steps in job.
                steps = job.get("steps", "")
                for i, step in enumerate(steps, start=1):
                    # Check for 'name' key for step.
                    if "name" not in step:
                        findings.append(
                            LintFinding(
                                f"Name key missing for step {str(i)} of job key '{job_key}'.",
                                "warning",
                            )
                        )
                    # Check for 'name' value to be capitalized in step.
                    elif not step["name"][0].isupper():
                        findings.append(
                            LintFinding(
                                f"Name value in step {str(i)} of job key '{job_key}' is not capitalized. [{step['name']}]",
                                "warning",
                            )
                        )

                    if "uses" in step:
                        try:
                            path, hash = step["uses"].split("@")
                        except ValueError:
                            logging.info("Skipping local action in workflow.")
                            break

                        # If the step has a 'uses' key, check value hash.
                        try:

                            # Check to make sure SHA1 hash is 40 characters.
                            if len(hash) != 40:
                                findings.append(
                                    LintFinding(
                                        f"Step {str(i)} of job key '{job_key}' does not have a valid action hash. (not 40 characters)",
                                        "error",
                                    )
                                )

                            # Attempts to convert the hash to a integer
                            # which will succeed if all characters are hexadecimal
                            try:
                                int(hash, 16)
                            except ValueError:
                                findings.append(
                                    LintFinding(
                                        f"Step {str(i)} of job key '{job_key}' does not have a valid action hash. (not all hexadecimal characters)",
                                        "error",
                                    )
                                )
                        except:
                            findings.append(
                                LintFinding(
                                    f"Step {str(i)} of job key '{job_key}' does not have a valid action hash. (missing '@' character)",
                                    "error",
                                )
                            )

                        # If the step has a 'uses' key, check path for external workflow
                        path_list = path.split("/", 2)

                        if "bitwarden" in path and len(path_list) < 3:
                            findings.append(
                                LintFinding(
                                    f"Step {str(i)} of job key '{job_key}' does not have a valid action path. (missing name of the repository or workflow)",
                                    "error",
                                )
                            )
                        elif len(path_list) < 2:
                            findings.append(
                                LintFinding(
                                    f"Step {str(i)} of job key '{job_key}' does not have a valid action path. (missing workflow name or the workflow author)",
                                    "error",
                                )
                            )
                        # Check if GitHub repository with action exists
                        elif not action_repo_exists(step["uses"]):
                            action_id = step["uses"]
                            findings.append(
                                LintFinding(
                                    f"Step {str(i)} of job key '{job_key}' uses an non-existing action: {action_id}.",
                                    "error",
                                )
                            )
                        else:
                            # If the step has a 'uses' key and path is correct, check the action id repo for an update.
                            update_available = get_action_update(step["uses"])
                            if update_available:
                                findings.append(
                                    LintFinding(
                                        f"Step {str(i)} of job key '{job_key}' uses an outdated action, consider updating it '{update_available}'.",
                                        "warning",
                                    )
                                )

                    # If the step has a 'run' key and only has one command, check if it's a single line.
                    if "run" in step:
                        if step["run"].count("\n") == 1:
                            findings.append(
                                LintFinding(
                                    f"Run in step {str(i)} of job key '{job_key}' should be a single line.",
                                    "warning",
                                )
                            )

    if len(findings) > 0:
        print("#", filename)
        for finding in findings:
            print_finding(finding)
        print()

    max_error_level = get_max_error_level(findings)

    return max_error_level


def main(input_args=None):

    # Pull the arguments from the command line
    if not input_args:
        input_args = sys.argv[1:]

    # Read arguments from command line.
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="file or directory input")
    parser.add_argument(
        "-s",
        "--strict",
        action="store_true",
        help="return non-zero exit code on warnings " "as well as errors",
    )
    args = parser.parse_args(input_args)
    # max_error_level = 0

    # for filename in input_files:
    #     prob_level = lint(filename)
    #     max_error_level = max(max_error_level, prob_level)
    input_files = workflow_files(args.input)
    if len(input_files) > 0:
        prob_levels = list(map(lint, input_files))

        max_error_level = max(prob_levels)

        if max_error_level == PROBLEM_LEVELS["error"]:
            return_code = 2
        elif max_error_level == PROBLEM_LEVELS["warning"]:
            return_code = 1 if args.strict else 0
        else:
            return_code = 0

        return return_code
    else:
        print(f'File(s)/Directory: "{args.input}" does not exist, exiting.')
        return -1


if __name__ == "__main__":
    return_code = main()
    sys.exit(return_code)
