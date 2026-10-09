package org.t7rekindle.server;

import com.tngtech.archunit.core.importer.ClassFileImporter;
import com.tngtech.archunit.core.importer.ImportOption;
import org.junit.jupiter.api.Test;
import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses;

class ArchitectureTest {
    @Test void servicesAndPersistenceDoNotDependOnTheWebLayer() {
        var classes = new ClassFileImporter().withImportOption(ImportOption.Predefined.DO_NOT_INCLUDE_TESTS)
                .importPackages("org.t7rekindle.server");
        noClasses().that().resideInAnyPackage("..service..", "..persistence..", "..domain..")
                .should().dependOnClassesThat().resideInAnyPackage("..web..", "jakarta.servlet..")
                .check(classes);
    }
}
