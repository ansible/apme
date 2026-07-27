import { useState } from 'react';
import { PageMasthead, PageThemeSwitcher, PageNotificationsIcon } from '@ansible/ansible-ui-framework';
import { PageMastheadDropdown } from '@ansible/ansible-ui-framework/PageMasthead/PageMastheadDropdown';
import {
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  DropdownItem,
  Modal,
  ModalBody,
  Title,
  ToolbarGroup,
  ToolbarItem,
} from '@patternfly/react-core';
import { QuestionCircleIcon } from '@patternfly/react-icons';

export function ApmeMasthead() {
  const [aboutOpen, setAboutOpen] = useState(false);

  return (
    <PageMasthead
      brand={
        <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <img src="/apme.svg" alt="" height={32} width={32} aria-hidden />
          <span style={{ fontWeight: 700, fontSize: 18, letterSpacing: 1.5 }}>
            APME
          </span>
        </span>
      }
    >
      <ToolbarItem style={{ flexGrow: 1 }} />
      <ToolbarGroup variant="action-group-plain">
        <ToolbarItem visibility={{ default: 'hidden', lg: 'visible' }}>
          <PageThemeSwitcher />
        </ToolbarItem>
        <ToolbarItem>
          <PageNotificationsIcon />
        </ToolbarItem>
        <ToolbarItem>
          <PageMastheadDropdown id="help-menu" icon={<QuestionCircleIcon />}>
            <DropdownItem
              id="docs"
              isExternalLink
              component="a"
              href="https://github.com/ansible/apme"
            >
              Documentation
            </DropdownItem>
            <DropdownItem
              id="about"
              onClick={() => setAboutOpen(true)}
            >
              About APME
            </DropdownItem>
          </PageMastheadDropdown>
        </ToolbarItem>
      </ToolbarGroup>

      <Modal
        className="apme-about-modal"
        isOpen={aboutOpen}
        onClose={() => setAboutOpen(false)}
        variant="medium"
        maxWidth="36rem"
        aria-label="About APME"
      >
        <ModalBody className="apme-about-modal__body">
          <div className="apme-about-modal__layout">
            <div className="apme-about-modal__brand">
              <img
                className="apme-about-modal__logo"
                src="/apme.svg"
                alt="APME logo"
              />
              <p className="apme-about-modal__copyright">
                Copyright {new Date().getFullYear()} Red Hat, Inc.
              </p>
            </div>
            <div className="apme-about-modal__info">
              <Title headingLevel="h1" size="2xl">
                APME
              </Title>
              <p className="apme-about-modal__tagline">
                Ansible Policy &amp; Modernization Engine
              </p>
              <DescriptionList isHorizontal isCompact>
                <DescriptionListGroup>
                  <DescriptionListTerm>Version</DescriptionListTerm>
                  <DescriptionListDescription>{__APME_VERSION__}</DescriptionListDescription>
                </DescriptionListGroup>
              </DescriptionList>
            </div>
          </div>
        </ModalBody>
      </Modal>
    </PageMasthead>
  );
}
