plugins {
    id("java")
    id("application")
    id("org.openjfx.javafxplugin") version "0.0.14"
}

group = "com.minelink"
version = "2.0.0"

java {
    // Toolchain can cause issues if not found/downloadable. Use local Java.
    // toolchain {
    //     languageVersion.set(JavaLanguageVersion.of(21))
    // }
}

repositories {
    mavenCentral()
}

javafx {
    version = "21"
    modules = listOf("javafx.controls", "javafx.fxml")
}

dependencies {
    // Netty for high-performance networking
    implementation("io.netty:netty-all:4.1.100.Final")
    
    // JSON serialization
    implementation("com.fasterxml.jackson.core:jackson-databind:2.16.0")
    
    // Logging
    implementation("org.slf4j:slf4j-api:2.0.9")
    implementation("ch.qos.logback:logback-classic:1.4.11")
    
    // Testing
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.0")
}

application {
    mainClass.set("com.minelink.MineLinkApp")
}

tasks.test {
    useJUnitPlatform()
}

// Create a fat JAR with all dependencies
tasks.register<Jar>("fatJar") {
    archiveClassifier.set("all")
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE
    
    manifest {
        attributes["Main-Class"] = "com.minelink.MineLinkApp"
    }
    
    from(sourceSets.main.get().output)
    
    dependsOn(configurations.runtimeClasspath)
    from({
        configurations.runtimeClasspath.get()
            .filter { it.name.endsWith("jar") }
            .map { zipTree(it) }
    })
}

// Create distribution zip
tasks.named<Zip>("distZip") {
    archiveFileName.set("MineLink-${version}.zip")
}
