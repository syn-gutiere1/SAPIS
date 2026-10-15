#!/usr/bin/env groovy
/**
 * TestHarness.groovy - runs REAL Groovy scripts from SAP Integration Suite iFlows.
 *
 * Why this exists: in the old pipeline the Groovy was never actually executed.
 * The `.bat` only compiled the stubs, and the generated tests "simulated" the
 * logic in Python (`simulate_condition_check()` and similar). In other words, a
 * real error in the `.groovy` never broke the build - the test passed while
 * validating a Python copy of the rule, not the rule.
 *
 * Usage:
 *
 *   groovy -cp stubs_classes TestHarness.groovy \
 *          --script   <file.groovy> \
 *          --testdata <directory/testdata> \
 *          [--junit  <junit-groovy.xml>] \
 *          [--filter <substring>]
 *
 * Both `.groovy` and `.gsh` are executed: SAP CPI uses either extension for a Groovy
 * script, and scanning only `.groovy` left a real script with no coverage at all.
 *
 * Fixtures live in a folder per script, named after the script without its extension
 * and sanitised to [A-Za-z0-9._-]: a script called `My Script (1).gsh` maps to
 * `My_Script__1_`. The harness prints the folder name to create when one is missing,
 * because the mapping is not guessable.
 *
 * File convention in --testdata:
 *
 *   <case>.xml             input payload (required)
 *   <case>.inject.json     optional: initial headers/properties
 *                          {"headers": {...}, "properties": {...}}
 *   <case>.assert.json     optional: checks after the run
 *                          {
 *                            "headers":             {"OrderCodeValid": "true"},
 *                            "body_contains":       ["YORB"],
 *                            "log_headers":         {"Country": "US"},
 *                            "attachments":         ["Payload"],
 *                            "attachment_contains": {"Payload": "YORB"}
 *                          }
 *
 * Without an assertion file, the test only confirms that the script runs
 * without throwing an exception.
 *
 * Exit codes: 0 = all passed, 1 = some failure, 2 = invalid usage.
 */

import com.sap.gateway.ip.core.customdev.util.MessageLogFactory
import com.sap.gateway.ip.core.customdev.util.MessageProcessingContext

// ---------------------------------------------------------------------------
// Utilities (defined before the main flow on purpose)
// ---------------------------------------------------------------------------

/** Escapes text for XML. Fixture names come from the repository, with no implicit trust. */
String escapeXml(String value) {
    if (value == null) {
        return ''
    }
    return value
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;')
}

Map parseArgs(String[] argv) {
    def args = [script: null, scanAll: null, testdata: null, junit: null,
                filter: null, snapshotMissing: false, requireFixtures: false]
    for (int i = 0; i < argv.length; i++) {
        switch (argv[i]) {
            case '--script':            args.script = argv[++i]; break
            case '--scan-all':          args.scanAll = argv[++i]; break
            case '--testdata':          args.testdata = argv[++i]; break
            case '--junit':             args.junit = argv[++i]; break
            case '--filter':            args.filter = argv[++i]; break
            case '--snapshot-missing':  args.snapshotMissing = true; break
            case '--require-fixtures':  args.requireFixtures = true; break
            default:
                System.err.println("Unknown argument: ${argv[i]}")
                System.exit(2)
        }
    }
    if (!args.script && !args.scanAll) {
        System.err.println('Usage: TestHarness.groovy --script <file.groovy> | --scan-all <directory> --testdata <directory>')
        System.exit(2)
    }
    if (!args.testdata) {
        System.err.println('The --testdata argument is required.')
        System.exit(2)
    }
    return args
}

/**
 * Extensions this harness executes.
 *
 * SAP CPI keeps Groovy scripts in the artifact as `.groovy` or `.gsh`, and both are
 * the same language with the same runtime contract. Scanning only `.groovy` left a
 * real production script, referenced by the `.iflw`, with no coverage at all - the
 * same class of blindness this harness exists to remove.
 *
 * Declared inside the method rather than as a static field: a static method in a
 * Groovy script cannot see a static field of the script class.
 */
static List<String> scriptExtensions() {
    return ['.groovy', '.gsh']
}

/** True when the file is a script this harness can execute. */
static boolean isScriptFile(File file) {
    if (!file.isFile()) return false
    def lower = file.name.toLowerCase()
    return scriptExtensions().any { lower.endsWith(it) }
}

/** The script name without its extension: `My Script (1).gsh` -> `My Script (1)`. */
static String scriptBaseName(File scriptFile) {
    def name = scriptFile.name
    def dot = name.lastIndexOf('.')
    return dot > 0 ? name.substring(0, dot) : name
}

/**
 * The fixture directory name for a script.
 *
 * The base name alone is not safe: SAP can hand us a script whose name carries spaces
 * or parentheses, and a directory with those is awkward to pass around and to match.
 * Anything outside `[A-Za-z0-9._-]` becomes `_`, so `My Script (1).gsh` maps to
 * `My_Script__1_`. The mapping is deterministic, so the name to create is never a
 * guess.
 */
static String fixtureDirName(File scriptFile) {
    scriptBaseName(scriptFile).replaceAll(/[^A-Za-z0-9._-]/, '_')
}

/**
 * Fixtures for one script.
 *
 * Convention: if a subdirectory of `testdata/` exists with the script name
 * (without the extension, sanitised), it takes priority. This lets each script have
 * its own fixtures, instead of every script consuming the same flat directory.
 *
 *   testdata/<ScriptName>/<case>.xml
 *   testdata/<case>.xml                 (fallback, flat layout)
 */
File fixturesDirFor(File testdataDir, File scriptFile) {
    def dedicated = new File(testdataDir, fixtureDirName(scriptFile))
    return dedicated.isDirectory() ? dedicated : testdataDir
}

// ---------------------------------------------------------------------------
// Mock of the Message interface (the stub defines the contract, the mock the implementation)
// ---------------------------------------------------------------------------

class MockMessage implements com.sap.gateway.ip.core.customdev.util.Message {
    Object body
    Map<String, Object> headers = [:]
    Map<String, Object> properties = [:]
    Map<String, Object> exchangeProperties = [:]

    Object getBody() { body }
    Object getBody(Class type) { body }
    void setBody(Object b) { body = b }

    Map<String, Object> getHeaders() { headers }
    void setHeaders(Map<String, Object> h) { headers = h }

    Object getHeader(String key) { headers[key] }
    Object getHeader(String key, String defaultValue) {
        headers.containsKey(key) ? headers[key] : defaultValue
    }
    void setHeader(String key, Object value) { headers[key] = value }

    Map<String, Object> getProperties() { properties }
    void setProperties(Map<String, Object> p) { properties = p }
    Object getProperty(String key) { properties[key] }
    Object getProperty(String key, String defaultValue) {
        properties.containsKey(key) ? properties[key] : defaultValue
    }
    void setProperty(String key, Object value) { properties[key] = value }

    Object getExchangeProperty(String key) { exchangeProperties[key] }
    Object getExchangeProperty(String key, String defaultValue) {
        exchangeProperties.containsKey(key) ? exchangeProperties[key] : defaultValue
    }
    void setExchangeProperty(String key, Object value) { exchangeProperties[key] = value }
}

// ---------------------------------------------------------------------------
// Main flow
// ---------------------------------------------------------------------------

def args = parseArgs(args)

def testdataDir = new File(args.testdata as String)
if (!testdataDir.isDirectory()) {
    System.err.println("testdata directory not found: ${testdataDir}")
    System.exit(2)
}

def json = new groovy.json.JsonSlurper()

// --- which scripts to run --------------------------------------------------
def scripts = []
if (args.scanAll) {
    def dir = new File(args.scanAll as String)
    if (!dir.isDirectory()) {
        System.err.println("Scripts directory not found: ${dir}")
        System.exit(2)
    }
    scripts = dir.listFiles()
            .findAll { isScriptFile(it) }
            .sort { it.name }
} else {
    def scriptFile = new File(args.script as String)
    if (!scriptFile.isFile()) {
        System.err.println("Script not found: ${scriptFile}")
        System.exit(2)
    }
    scripts = [scriptFile]
}

if (scripts.isEmpty()) {
    println "::warning::No script found (looked for ${scriptExtensions().join(', ')})."
}

// --- execution -------------------------------------------------------------
// results[i] = [script: File, cases: [[name, failures, millis], ...]]
def results = []
long totalMillis = 0
int snapshotsCreated = 0
// Scripts without a reviewed fixture, when --require-fixtures is active.
def pending = []

scripts.each { scriptFile ->

    // `fixturesDirFor` always returns an existing directory: it falls back to
    // `--testdata` itself, which is validated before this loop. So "the directory is
    // missing" cannot happen here - the real case is "there is no payload for this
    // script", handled below. The sanitised folder name belongs in that message,
    // because a name like `My Script (1).gsh` maps to `My_Script__1_`.
    def fixturesDir = fixturesDirFor(testdataDir, scriptFile)

    // Payloads are included; the assertion and injection sidecars are not.
    def fixtures = fixturesDir.listFiles()
            .findAll { it.isFile() }
            .findAll { !it.name.endsWith('.assert.json') && !it.name.endsWith('.inject.json') }
            .sort { it.name }

    if (args.filter) {
        fixtures = fixtures.findAll { it.name.contains(args.filter as String) }
    }

    if (fixtures.isEmpty()) {
        // In CI it makes no sense to create a snapshot: the file would be born on the
        // runner and would never be committed, so the next run would repeat
        // everything. `--require-fixtures` therefore demands that the fixtures were
        // created and reviewed locally.
        def folder = fixtureDirName(scriptFile)
        def warning = "No fixture for ${scriptFile.name} in ${fixturesDir} " +
                "(expected a folder named '${folder}')"
        if (args.requireFixtures) {
            println "::error::${warning}. Run the harness locally with --snapshot-missing and version the fixtures."
            pending << warning
            return
        }
        println "::warning::${warning}"
        return
    }

    def cases = []

    fixtures.each { fixture ->

        def baseName = fixture.name.replaceFirst(/\.[^.]+$/, '')
        def testName = "${fixtureDirName(scriptFile)} :: ${baseName}".toString()

        def assertFile = new File(fixturesDir, "${baseName}.assert.json")
        def injectFile = new File(fixturesDir, "${baseName}.inject.json")

        def expectations = assertFile.isFile() ? json.parseText(assertFile.getText('UTF-8')) : null
        def injections = injectFile.isFile() ? json.parseText(injectFile.getText('UTF-8')) : null

        def message = new MockMessage(body: fixture.getText('UTF-8'))
        def logFactory = new MessageLogFactory()

        if (injections?.headers) {
            injections.headers.each { key, value -> message.setHeader(key as String, value) }
        }
        if (injections?.properties) {
            injections.properties.each { key, value -> message.setProperty(key as String, value) }
        }

        def failures = []
        def started = System.currentTimeMillis()

        try {
            def binding = new Binding()
            // The tenant runtime injects this factory into the script binding.
            binding.setVariable('messageLogFactory', logFactory)

            def shell = new GroovyShell(this.class.classLoader, binding)
            def compiled = shell.parse(scriptFile)
            def context = new MessageProcessingContext()

            // Some scripts declare processData(Message), others
            // processData(Message, MessageProcessingContext).
            if (compiled.metaClass.respondsTo(compiled, 'processData', message, context)) {
                compiled.processData(message, context)
            } else if (compiled.metaClass.respondsTo(compiled, 'processData', message)) {
                compiled.processData(message)
            } else {
                failures << 'The script exposes neither processData(Message) nor processData(Message, Context).'
            }
        } catch (Throwable error) {
            failures << "${error.class.simpleName}: ${error.message}"
        }

        def elapsed = System.currentTimeMillis() - started
        totalMillis += elapsed

        // Without an assertion file, the test only confirms that the script runs.
        // With --snapshot-missing, it writes the OBSERVED behavior for a human to
        // review.
        //
        // The snapshot carries the key "__review__", and while that key exists the
        // case fails: a snapshot describes what the script DOES, not what it
        // SHOULD do. Without that lock, a real defect would be frozen as an
        // expectation and the test would pass forever.
        if (!assertFile.isFile() && args.snapshotMissing && failures.isEmpty()) {
            def snapshot = ['__review__': 'Snapshot generated automatically. Confirm the values and remove this key.']
            def observedHeaders = [:]
            message.getHeaders().each { k, v -> observedHeaders[k as String] = v?.toString() }
            if (!observedHeaders.isEmpty()) {
                snapshot.headers = observedHeaders
            }
            def logRecord = logFactory.getRecordedLog()
            if (logRecord && !logRecord.getCustomHeaderPropertyMap().isEmpty()) {
                snapshot.log_headers = logRecord.getCustomHeaderPropertyMap()
            }
            if (logRecord && !logRecord.getAttachments().isEmpty()) {
                snapshot.attachments = logRecord.getAttachments()
            }
            assertFile.setText(
                    groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(snapshot)), 'UTF-8')
            snapshotsCreated = snapshotsCreated + 1
            expectations = snapshot
        }

        // --- checks --------------------------------------------------------
        if (expectations && failures.isEmpty()) {
            if (expectations.containsKey('__review__')) {
                failures << ('snapshot awaiting human review: confirm the observed ' +
                        'values and remove the "__review__" key from the .assert.json file')
            }

            expectations.headers?.each { key, expected ->
                def actual = message.getHeader(key as String)
                if (actual?.toString() != expected?.toString()) {
                    failures << "header '${key}': expected '${expected}', got '${actual}'"
                }
            }

            expectations.body_contains?.each { snippet ->
                def body = message.getBody()?.toString() ?: ''
                if (!body.contains(snippet as String)) {
                    failures << "body does not contain '${snippet}'"
                }
            }

            if (expectations.log_headers) {
                def recorded = logFactory.getRecordedLog()
                def recordedMap = recorded ? recorded.getCustomHeaderPropertyMap() : [:]
                expectations.log_headers.each { key, expected ->
                    if (recordedMap[key as String]?.toString() != expected?.toString()) {
                        failures << "log '${key}': expected '${expected}', got '${recordedMap[key as String]}'"
                    }
                }
            }

            // Attachments written through the MessageLog stub. A script such as
            // The payload logger exists only to attach the body, so without this check
            // the only thing checked about it would be that it did not throw.
            if (expectations.attachments) {
                def recorded = logFactory.getRecordedLog()
                def actual = recorded ? recorded.getAttachments() : []
                expectations.attachments.each { expected ->
                    if (!actual.contains(expected as String)) {
                        failures << "attachment '${expected}' was not added (added: ${actual})"
                    }
                }
            }

            if (expectations.attachment_contains) {
                def recorded = logFactory.getRecordedLog()
                def contents = recorded ? recorded.getAttachmentContent() : [:]
                expectations.attachment_contains.each { name, snippet ->
                    def content = contents[name as String]
                    if (content == null) {
                        failures << "attachment '${name}' has no recorded content"
                    } else if (!content.contains(snippet as String)) {
                        failures << "attachment '${name}' does not contain '${snippet}'"
                    }
                }
            }
        }

        cases << [name: testName, failures: failures, millis: elapsed]
    }

    if (!cases.isEmpty()) {
        results << [script: scriptFile, cases: cases]
    }
}

if (snapshotsCreated > 0) {
    println "::notice::${snapshotsCreated} assertion snapshot(s) created. Review and version them."
}

// Flattened for the counts and the JUnit.
def allCases = []
results.each { r -> r.cases.each { allCases << it } }

// --- output ----------------------------------------------------------------
// Counting done with explicit loops: `results.count { }` and `results.size()`
// inside GString interpolation produce `null` in some combinations of Groovy
// version, which corrupted the summary shown in CI.
int passed = 0
int failed = 0
allCases.each { r ->
    if (r.failures.isEmpty()) {
        passed = passed + 1
    } else {
        failed = failed + 1
    }
}
int totalCases = allCases.size()

allCases.each { r ->
    def status = r.failures.isEmpty() ? 'PASS' : 'FAIL'
    println "[${status}] ${r.name} (${r.millis} ms)"
    r.failures.each { println "         -> ${it}" }
}

println ''
println "Summary: ${passed} passed, ${failed} failed, ${totalCases} total"

if (!pending.isEmpty()) {
    println ''
    println "${pending.size()} script(s) without a reviewed fixture:"
    pending.each { println "  - ${it}" }
}

if (args.junit) {
    def junitFile = new File(args.junit as String)
    junitFile.parentFile?.mkdirs()

    // XML built by explicit concatenation. Inside a multiline GString template,
    // interpolated numeric values came out as "[null]" in this version of Groovy,
    // which corrupted the JUnit attributes consumed by dorny/test-reporter.
    //
    // One <testsuite> per script: that way several scripts coexist in a single
    // report without case name collisions, which lets the workflow run the
    // harness only once.
    StringBuilder sb = new StringBuilder()
    sb.append('<?xml version="1.0" encoding="UTF-8"?>\n')
    sb.append('<testsuites name="groovy-harness" tests="').append(totalCases)
    sb.append('" failures="').append(failed)
    sb.append('" errors="0" time="').append(totalMillis / 1000.0).append('">\n')

    results.each { group ->
        def scriptName = group.script.name
        int groupFailures = 0
        group.cases.each { c -> if (!c.failures.isEmpty()) { groupFailures = groupFailures + 1 } }

        sb.append('  <testsuite name="').append(escapeXml(scriptName))
        sb.append('" tests="').append(group.cases.size())
        sb.append('" failures="').append(groupFailures)
        sb.append('" errors="0" time="0">\n')

        group.cases.each { c ->
            sb.append('    <testcase classname="').append(escapeXml(scriptName))
            sb.append('" name="').append(escapeXml(c.name as String))
            sb.append('" time="').append(c.millis / 1000.0).append('">')
            if (!c.failures.isEmpty()) {
                sb.append('<failure message="').append(escapeXml(c.failures.join(' | ')))
                sb.append('">').append(escapeXml(c.failures.join('\n'))).append('</failure>')
            }
            sb.append('</testcase>\n')
        }

        sb.append('  </testsuite>\n')
    }

    sb.append('</testsuites>\n')

    junitFile.setText(sb.toString(), 'UTF-8')
    println "JUnit XML: ${junitFile}"
}

/**
 * Record the verdict in a file, so the CI job does not have to infer it.
 *
 * The harness knows the outcome; the shell around it had to guess it from the exit
 * status of a pipeline, and that guess failed in CI: the harness reported
 * `Summary: 3 passed, 1 failed` and exited 1, while `${PIPESTATUS[0]}` read 0, so no
 * marker was written and the job treated a red harness as a pass.
 *
 * `HARNESS_FAILURE_MARKER` carries the path to write when the run failed. When the
 * variable is absent - a local run - nothing is written.
 *
 * Returns true when a marker was written.
 */
static boolean writeFailureMarker(String reason) {
    def path = System.getenv('HARNESS_FAILURE_MARKER')
    if (!path) return false
    try {
        def file = new File(path)
        file.parentFile?.mkdirs()
        file.setText(reason + System.lineSeparator(), 'UTF-8')
        println "Failure marker written: ${file} (${reason})"
        return true
    } catch (Exception e) {
        // Never fail the run because the marker could not be written: the exit status
        // still reports the verdict.
        System.err.println("Could not write the failure marker ${path}: ${e.message}")
        return false
    }
}

// Exit 0 only when every case passed AND there is no script without a reviewed
// fixture (when --require-fixtures was used).
def verdictFailed = (failed != 0 || !pending.isEmpty())
if (verdictFailed) {
    def reason = failed != 0
            ? "${failed} of ${totalCases} case(s) failed"
            : "${pending.size()} script(s) without a reviewed fixture"
    writeFailureMarker(reason)
}
System.exit(verdictFailed ? 1 : 0)

